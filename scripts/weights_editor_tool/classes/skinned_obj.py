import glob
import os
import random
import sys

if sys.version_info < (3, 0):
    import cPickle
else:
    import _pickle as cPickle

from maya import cmds
from maya import OpenMaya
from maya.api import OpenMaya as om2

from weights_editor_tool.widgets.widgets_utils import *

from weights_editor_tool import constants
from weights_editor_tool.enums import ColorTheme
from weights_editor_tool import weights_editor_utils as utils
from weights_editor_tool.widgets import status_progress_bar
from weights_editor_tool.classes.skin_data import SkinData


class SkinnedObj:

    last_browsing_path = None

    def __init__(self, obj):
        self.name = obj
        self.skin_cluster = None
        self.skin_data = None
        self.vert_count = 0
        self.infs = []
        self.inf_colors = {}

        if self.is_valid():
            self.vert_count = utils.get_vert_count(self.name)
            self.update_skin_data()

    @classmethod
    def create(cls, obj):
        return cls(obj)

    @classmethod
    def create_empty(cls):
        return cls(None)

    @classmethod
    def _launch_file_picker(cls, file_mode, caption, file_name="", ext="skin", ok_caption="OK"):
        if cls.last_browsing_path is None:
            cls.last_browsing_path = cmds.workspace(q=True, fullName=True)

        last_path = cls.last_browsing_path
        if file_mode != 3:
            last_path = os.path.join(last_path, file_name + "." + ext)

        picked_path = cmds.fileDialog2(
            caption=caption,
            fileMode=file_mode,
            fileFilter="*.{}".format(ext),
            dir=last_path,
            okCaption=ok_caption)

        if picked_path:
            if file_mode == 3:
                cls.last_browsing_path = picked_path[0]
            else:
                cls.last_browsing_path = os.path.dirname(picked_path[0])
            
            return picked_path[0]

    @staticmethod
    def _find_influence_by_name(long_name):
        short_name = long_name.split("|")[-1]
        objs = cmds.ls(short_name)

        if objs:
            if long_name in objs:
                return long_name
            else:
                return objs[0]

    @staticmethod
    def _get_dag_path(obj):
        msel_list = om2.MSelectionList()
        msel_list.add(obj)
        return msel_list.getDagPath(0)

    @classmethod
    def _to_mfn_mesh(cls, mesh):
        dag_path = cls._get_dag_path(mesh)
        return om2.MFnMesh(dag_path)

    @classmethod
    def _to_mfn_nurbs_curve(cls, curve):
        dag_path = cls._get_dag_path(curve)
        return om2.MFnNurbsCurve(dag_path)

    def _get_world_points(self, space=om2.MSpace.kWorld):
        if cmds.listRelatives(self.name, shapes=True, type="mesh"):
            mfn_mesh = self._to_mfn_mesh(self.name)
            return mfn_mesh.getPoints(space)
        elif cmds.listRelatives(self.name, shapes=True, type="nurbsCurve"):
            mfn_nurbs_curve = self._to_mfn_nurbs_curve(self.name)
            return mfn_nurbs_curve.cvPositions(space)
        else:
            raise NotImplementedError("This object's type is not supported: {0}".format(self.name))

    def _map_to_closest_vertexes(self, verts_data, vert_filter=[]):
        weights_data = {}

        file_points = [
            om2.MPoint(*verts_data[index]["world_pos"])
            for index in sorted(verts_data.keys())
        ]

        # Build a temporary new mesh from the file's positions so that it's exposed to the api.
        temp_mfn_mesh = om2.MFnMesh()
        temp_mfn_mesh.addPolygon(file_points, False, 0)
        new_mesh = om2.MFnDagNode(temp_mfn_mesh.parent(0)).fullPathName()
        file_mfn_mesh = self._to_mfn_mesh(new_mesh)

        try:
            mesh_points = self._get_world_points()

            with status_progress_bar.StatusProgressBar("Finding closest points", len(mesh_points)) as pbar:
                for vert_index, point in enumerate(mesh_points):
                    try:
                        # Skip calculations if index is not in the filter.
                        if vert_filter and vert_index not in vert_filter:
                            continue

                        # Get the closest face.
                        closest_point = file_mfn_mesh.getClosestPoint(point, om2.MSpace.kWorld)
                        face_index = closest_point[1]

                        # Get face's vertexes and get the closest vertex.
                        face_vertexes = file_mfn_mesh.getPolygonVertices(face_index)

                        vert_distances = [
                            (index, file_points[index].distanceTo(closest_point[0]))
                            for index in face_vertexes
                        ]

                        closest_index = min(vert_distances, key=lambda dist: dist[1])[0]
                        weights_data[vert_index] = closest_index

                        if pbar.was_cancelled():
                            raise RuntimeError("User cancelled")
                    finally:
                        pbar.next()
        finally:
            if cmds.objExists(new_mesh):
                cmds.undoInfo(stateWithoutFlush=False)
                cmds.delete(new_mesh)
                cmds.undoInfo(stateWithoutFlush=True)

        return weights_data

    def is_valid(self):
        return self.name is not None and cmds.objExists(self.name)

    def has_valid_skin(self):
        return self.skin_cluster is not None and self.has_skin_data()

    def short_name(self):
        return self.name.split("|")[-1]

    def update_skin_data(self):
        self.skin_cluster = None
        self.skin_data = SkinData.create_empty()

        if self.is_valid():
            self.skin_cluster = utils.get_skin_cluster(self.name)

            if self.skin_cluster:
                self.skin_data = SkinData.get(self.skin_cluster)
                self.collect_influence_colors()
                self.infs = self.get_all_infs()

    def is_skin_corrupt(self):
        """
        Checks if topology changes were done after the skinCluster was applied.
        """
        vert_count = utils.get_vert_count(self.name)
        weights_count = len(cmds.getAttr("{0}.weightList[*]".format(self.skin_cluster)))
        return vert_count != weights_count

    def get_all_infs(self):
        """
        Gets and returns a list of all influences from the active skinCluster.
        Also collects unique colors of each influence for the Softimage theme.
        """
        return sorted(utils.get_influences(self.skin_cluster))

    def select_inf_vertexes(self, infs):
        """
        Selects effected vertexes by supplied influences.

        Args:
            infs(string[]): List of influences to select from.
        """
        infs_set = set(infs)
        effected_verts = set()

        for vert_index in self.skin_data:
            vert_infs = self.skin_data[vert_index]["weights"].keys()

            is_effected = infs_set.intersection(vert_infs)
            if is_effected:
                if utils.is_curve(self.name):
                    effected_verts.add("{0}.cv[{1}]".format(self.name, vert_index))
                else:
                    effected_verts.add("{0}.vtx[{1}]".format(self.name, vert_index))

        cmds.select(list(effected_verts))

    def flood_weights_to_closest(self):
        """
        Each vertex will be assigned a full weight to its closest joint.
        """
        influences = self.get_influence_ids()

        inf_positions = {
            key: cmds.xform(inf, q=True, ws=True, t=True)
            for key, inf in influences.items()
        }

        verts = cmds.ls("{}.vtx[*]".format(self.name), flatten=True)

        vert_inf_mappings = {}

        for vert_index, plug, in enumerate(verts):
            vert_pos = cmds.pointPosition(plug, world=True)
            vert_point = OpenMaya.MPoint(*vert_pos)

            closest_inf_index = None
            closest_inf_dist = 0

            for inf_index in inf_positions:
                inf_point = OpenMaya.MPoint(*inf_positions[inf_index])
                dist = vert_point.distanceTo(inf_point)
                if closest_inf_index is None or dist < closest_inf_dist:
                    closest_inf_index = inf_index
                    closest_inf_dist = dist

            vert_inf_mappings[vert_index] = closest_inf_index

        cmds.setAttr("{0}.nw".format(self.skin_cluster), 0)
        cmds.skinPercent(self.skin_cluster, verts, prw=100, nrm=0)

        for vert_index, inf_index in vert_inf_mappings.items():
            weight_plug = "{0}.weightList[{1}].weights[{2}]".format(self.skin_cluster, vert_index, inf_index)
            cmds.setAttr(weight_plug, 1)

        cmds.setAttr("{0}.nw".format(self.skin_cluster), 1)
        cmds.skinCluster(self.skin_cluster, e=True, forceNormalizeWeights=True)

    def prune_weights(self, value):
        """
        Runs prune weights on selected vertexes on supplied object.

        Args:
            value(float): Removes any weights below this value.

        Returns:
            True on success.
        """
        flatten_list = utils.get_vert_indexes(self.name)
        if not flatten_list:
            OpenMaya.MGlobal.displayError("No vertexes are selected.")
            return False

        cmds.skinPercent(self.skin_cluster, flatten_list, prw=value, nrm=True)

        return True

    def prune_max_infs(self, max_inf_count, vert_filter=[]):
        if not vert_filter:
            OpenMaya.MGlobal.displayError("No vertexes are selected.")
            return False

        for vert_index in self.skin_data:
            if vert_filter and vert_index not in vert_filter:
                continue

            sorted_infs = [
                inf for inf, value in sorted(self.skin_data[vert_index]["weights"].items(), key=lambda item: item[1])]

            for inf in sorted_infs:
                infs_count = len(self.skin_data[vert_index]["weights"])
                if infs_count <= max_inf_count:
                    break

                locked = cmds.getAttr("{0}.lockInfluenceWeights".format(inf))
                if locked:
                    continue

                self.skin_data.update_weight_value(vert_index, inf, 0)

        return True

    def mirror_skin_weights(self, mirror_mode, mirror_inverse, surface_association, inf_association=None, vert_filter=[]):
        objs = self.name
        if vert_filter:
            objs = [
                "{0}.vtx[{1}]".format(self.name, index)
                for index in vert_filter
            ]

        if inf_association is None:
            inf_association = "closestJoint"

        cmds.copySkinWeights(
            objs,
            mirrorMode=mirror_mode,
            mirrorInverse=mirror_inverse,
            surfaceAssociation=surface_association,
            influenceAssociation=[inf_association, "closestJoint"])

    def display_influence(self, influence, color_style=ColorTheme.Max, vert_filter=[]):
        """
        Colors a mesh to visualize skin data.

        Args:
            influence(string): Name of influence to display.
            color_style(int): 0=Max theme, 1=Maya theme.
            vert_filter(int[]): List of vertex indexes to only operate on.
        """
        if color_style == ColorTheme.Max:
            # Max
            low_rgb = [0, 0, 1]
            mid_rgb = [0, 1, 0]
            end_rgb = [1, 0, 0]
            no_rgb = [0.05, 0.05, 0.05]
            full_rgb = [1, 1, 1]
        elif color_style == ColorTheme.Maya:
            # Maya
            low_rgb = [0.5, 0, 0]
            mid_rgb = [1, 0.5, 0]
            end_rgb = [1, 1, 0]
            no_rgb = [0, 0, 0]
            full_rgb = [1, 1, 1]
        else:
            low_rgb = [0, 0, 0]
            mid_rgb = [0, 0, 0]
            end_rgb = [0, 0, 0]
            no_rgb = [0, 0, 0]
            full_rgb = [0, 0, 0]

        vert_colors = []
        vert_indexes = []

        for vert_index in self.skin_data:
            if vert_filter and vert_index not in vert_filter:
                continue

            weights_data = self.skin_data[vert_index]["weights"]

            if influence in weights_data:
                weight_value = weights_data[influence]
                rgb = utils.get_weight_color(
                    weight_value,
                    start_color=low_rgb,
                    mid_color=mid_rgb,
                    end_color=end_rgb,
                    full_color=full_rgb)
            else:
                rgb = no_rgb

            vert_colors.append(rgb)
            vert_indexes.append(vert_index)

        utils.apply_vert_colors(self.name, vert_colors, vert_indexes)

    def display_multi_color_influence(self, vert_filter=[]):
        """
        Mimics Softimage and displays all influences at once with their own unique color.

        Args:
            vert_filter(int[]): List of vertex indexes to only operate on.

        Returns:
            A dictionary of {inf_name:[r, g, b]...}
        """

        if self.inf_colors is None:
            self.collect_influence_colors()

        vert_colors = []
        vert_indexes = []

        for vert_index in self.skin_data:
            if vert_filter and vert_index not in vert_filter:
                continue

            final_color = [0, 0, 0]

            for inf, weight in self.skin_data[vert_index]["weights"].items():
                inf_color = self.inf_colors.get(inf)
                final_color[0] += inf_color[0] * weight
                final_color[1] += inf_color[1] * weight
                final_color[2] += inf_color[2] * weight

            vert_colors.append(final_color)
            vert_indexes.append(vert_index)

        utils.apply_vert_colors(self.name, vert_colors, vert_indexes)

    def display_max_influences(self, max_inf_count, vert_filter=[]):
        """
        Displays verts that are over the supplied maximum inflluence count.

        Args:
            max_inf_count(int): Color the vertex if it's over this number.
            vert_filter(int[]): List of vertex indexes to only operate on.

        Returns:
            A dictionary of {inf_name:[r, g, b]...}
        """
        vert_colors = []
        vert_indexes = []

        for vert_index in self.skin_data:
            if vert_filter and vert_index not in vert_filter:
                continue

            inf_count = len(self.skin_data[vert_index]["weights"])

            if inf_count > max_inf_count:  # Over the count.
                final_color = [1, 0, 0]
            else:
                final_color = [0, 0, 0]  # Under the count.

            vert_colors.append(final_color)
            vert_indexes.append(vert_index)

        utils.apply_vert_colors(self.name, vert_colors, vert_indexes)

    def average_by_neighbours(self, vert_index, strength):
        """
        Averages weights of surrounding vertexes.

        Args:
            vert_index(int)
            strength(int): A value of 0-1

        Returns:
            A dictionary of the new weights. {int_name:weight_value...}
        """
        old_weights = self.skin_data[vert_index]["weights"]
        new_weights = {}

        # Collect unlocked infs and total value of unlocked weights
        unlocked = []
        total = 0.0

        for inf in old_weights:
            is_locked = cmds.getAttr("{0}.lockInfluenceWeights".format(inf))
            if is_locked:
                new_weights[inf] = old_weights[inf]
            else:
                unlocked.append(inf)
                total += old_weights[inf]

        # Need at least 2 unlocked influences to continue
        if len(unlocked) < 2:
            return old_weights

        # Add together weight of each influence from neighbours
        neighbours = utils.get_vert_neighbours(self.name, vert_index)

        for index in neighbours:
            for inf, value in self.skin_data[index]["weights"].items():
                # Ignore if locked
                if inf not in unlocked:
                    continue

                # Add weight
                if inf not in new_weights:
                    new_weights[inf] = 0.0

                new_weights[inf] += value

        # Get sum of all new weight values
        total_all = sum([
            new_weights[inf]
            for inf in new_weights
            if inf in unlocked
        ])

        # Average values
        if total_all:
            for inf in new_weights:
                if inf in unlocked:
                    new_weight = new_weights[inf] * (total / total_all)
                    new_weights[inf] = old_weights[inf] + (new_weight - old_weights[inf]) * strength

        return new_weights

    def smooth_weights(self, vert_indexes, strength, normalize_weights=True):
        """
        Runs an algorithm to smooth weights on supplied vertex indexes.

        Args:
            vert_indexes(int[])
            strength(int): A value of 0-1
            normalize_weights(bool)
        """
        # Don't set new weights right away so new values don't interfere
        # when calculating other indexes.
        weights_to_set = {}
        for vert_index in vert_indexes:
            new_weights = self.average_by_neighbours(vert_index, strength)
            weights_to_set[vert_index] = new_weights

        # Set weights
        for vert_index, weights in weights_to_set.items():
            self.skin_data[vert_index]["weights"] = weights

        self.apply_current_skin_weights(vert_indexes, normalize=normalize_weights)

    def hide_vert_colors(self):
        if self.is_valid():
            utils.toggle_display_colors(self.name, False)
            utils.delete_temp_inputs(self.name)

    def switch_to_color_set(self):
        """
        Switches supplied object's color set to display skin weights.
        Needs to do this otherwise we risk overwriting another color set.

        Args:
            obj(string)
        """
        color_set_name = "weightsEditorColorSet"

        obj_shapes = cmds.listRelatives(self.name, f=True, shapes=True) or []
        old_color_sets = set(cmds.ls(cmds.listHistory(obj_shapes), type="createColorSet"))

        obj_color_sets = cmds.polyColorSet(self.name, q=True, allColorSets=True) or []

        if color_set_name not in obj_color_sets:
            cmds.polyColorSet(self.name, create=True, clamped=False, representation="RGB", colorSet=color_set_name)

        cmds.polyColorSet(self.name, currentColorSet=True, colorSet=color_set_name)

        new_color_sets = set(cmds.ls(cmds.listHistory(obj_shapes), type="createColorSet"))

        dif_color_sets = list(new_color_sets.difference(old_color_sets))
        if dif_color_sets:
            cmds.addAttr(dif_color_sets[0], ln=constants.COLOR_SET, dt="string")
            cmds.rename(dif_color_sets[0], constants.COLOR_SET)

    def has_skin_data(self):
        if self.skin_data is not None and self.skin_data.data:
            return True
        return False

    def get_influence_ids(self):
        return utils.get_influence_ids(self.skin_cluster)

    def collect_influence_colors(self, sat=250, brightness=150):
        """
        Generates a unique color for each influence.
        {inf_name:[r, g, b]...}

        Args:
            sat(float)
            brightness(float)
        """
        infs = self.get_all_infs()
        random.seed(0)
        random.shuffle(infs)

        inf_colors = {}

        hue_step = 360.0 / (len(infs))

        for i, inf in enumerate(infs):
            color = QColor()
            color.setHsv(hue_step * i, sat, brightness)
            color.toRgb()

            inf_colors[inf] = [
                color.red() / 255.0,
                color.green() / 255.0,
                color.blue() / 255.0]

        self.inf_colors = inf_colors

    def apply_current_skin_weights(self, vert_indexes, normalize=False, display_progress=False):
        """
        Sets skin weights with the supplied data.

        Args:
            vert_indexes(int[]): List of vertex indexes to only operate on.
            normalize(bool): Forces weights to be normalized.
            display_progress(bool): Displays a progress bar if enabled.
        """
        # Get influence info to map with
        inf_data = self.get_influence_ids()
        inf_ids = list(inf_data.keys())
        inf_names = list(inf_data.values())

        # Remove all existing weights
        if utils.is_curve(self.name):
            plug = "{0}.cv".format(self.name)
        else:
            plug = "{0}.vtx".format(self.name)

        selected_vertexes = [
            "{0}[{1}]".format(plug, index)
            for index in vert_indexes
        ]

        cmds.setAttr("{0}.nw".format(self.skin_cluster), 0)
        cmds.skinPercent(self.skin_cluster, selected_vertexes, prw=100, nrm=0)

        if display_progress:
            pbar = status_progress_bar.StatusProgressBar("Setting skin weights", len(vert_indexes))
            pbar.start()

        try:
            # Apply weights per vert
            for vert_index in vert_indexes:
                weight_list_attr = "{0}.weightList[{1}]".format(self.skin_cluster, vert_index)

                for inf_name, weight_value in self.skin_data[vert_index]["weights"].items():
                    index = inf_names.index(inf_name)
                    weight_attr = ".weights[{0}]".format(inf_ids[index])
                    cmds.setAttr("{0}{1}".format(weight_list_attr, weight_attr), weight_value)

                # Apply dual-quarternions
                dq_value = self.skin_data[vert_index]["dq"]
                cmds.setAttr("{0}.bw[{1}]".format(self.skin_cluster, vert_index), dq_value)

                if display_progress:
                    if pbar.was_cancelled():
                        break
                    pbar.next()
        finally:
            if display_progress:
                pbar.end()

        # Re-enable weights normalizing
        cmds.setAttr("{0}.nw".format(self.skin_cluster), 1)

        if normalize:
            cmds.skinCluster(self.skin_cluster, e=True, forceNormalizeWeights=True)

    def serialize(self):
        if not self.has_valid_skin():
            raise RuntimeError("Unable to detect a skinCluster on '{}'.".format(self.name))

        skin_data = self.skin_data.copy()
        mesh_points = self._get_world_points()

        with status_progress_bar.StatusProgressBar("Saving vert positions", len(mesh_points)) as pbar:
            for vert_index, pnt in enumerate(mesh_points):
                skin_data[vert_index]["world_pos"] = [pnt.x, pnt.y, pnt.z]

                if pbar.was_cancelled():
                    raise RuntimeError("User cancelled")

                pbar.next()

        influence_data = {}
        influence_ids = self.get_influence_ids()

        with status_progress_bar.StatusProgressBar("Saving influence positions", len(influence_ids)) as pbar:
            for inf_id, inf in influence_ids.items():
                influence_data[inf_id] = {
                    "name": inf,
                    "world_matrix": cmds.xform(inf, q=True, ws=True, m=True)
                }

                if pbar.was_cancelled():
                    raise RuntimeError("User cancelled")

                pbar.next()

        return {
            "version": constants.EXPORT_VERSION,
            "object": self.name,
            "verts": skin_data.data,
            "influences": influence_data,
            "skin_cluster": {
                "name": self.skin_cluster,
                "vert_count": cmds.polyEvaluate(self.name, vertex=True),
                "influence_count": len(influence_ids),
                "max_influences": cmds.getAttr("{}.maxInfluences".format(self.skin_cluster)),
                "skinning_method": cmds.getAttr("{}.skinningMethod".format(self.skin_cluster)),
                "dqs_support_non_rigid": cmds.getAttr("{}.dqsSupportNonRigid".format(self.skin_cluster))
            }
        }

    def import_skin(self, file_path=None, world_space=False, create_missing_infs=True):
        """
        Imports skin weights from a file.

        Args:
            file_path(string): An absolute path to save weights to.
            world_space(bool): False=loads by point order, True=loads by world positions
            create_missing_infs(bool): Create any missing influences so the skin can still import.
        """
        if not self.is_valid():
            raise RuntimeError("Need to pick an object first.")

        if file_path is None:
            file_path = self._launch_file_picker(1, "Import skin", ok_caption="Import")
            if not file_path:
                return False

        vert_filter = utils.extract_indexes(
            utils.get_vert_indexes(self.name))

        # Must have an existing skin cluster if we're only applying on some vertexes.
        if vert_filter:
            if not self.has_valid_skin():
                raise RuntimeError("A skinCluster must already exist when importing weights onto vertexes")

        with open(file_path, "rb") as f:
            skin_data = cPickle.loads(f.read())

            # Keys need to be converted to ints.
            skin_data["verts"] = {
                int(key): value
                for key, value in skin_data["verts"].items()
            }

        # Rename influences to match scene.
        with status_progress_bar.StatusProgressBar("Matching influences", len(skin_data["verts"])) as pbar:
            infs = {}

            for index in skin_data["verts"]:
                for old_name in list(skin_data["verts"][index]["weights"]):
                    if old_name not in infs:
                        infs[old_name] = self._find_influence_by_name(old_name) or old_name.split("|")[-1]
                    skin_data["verts"][index]["weights"][infs[old_name]] = skin_data["verts"][index]["weights"].pop(old_name)

                if pbar.was_cancelled():
                    raise RuntimeError("User cancelled")

                pbar.next()

        if world_space:
            closest_vertexes = self._map_to_closest_vertexes(skin_data["verts"], vert_filter)

            weights_data = {
                source_index: skin_data["verts"][file_index]
                for source_index, file_index in closest_vertexes.items()
            }
        else:
            # Bail if vert count with file and object don't match (import via point order only)
            file_vert_count = skin_data["skin_cluster"]["vert_count"]
            obj_vert_count = cmds.polyEvaluate(self.name, vertex=True)
            if file_vert_count != obj_vert_count:
                raise RuntimeError("Vert count doesn't match. (Object: {}, File: {})".format(obj_vert_count, file_vert_count))
            weights_data = skin_data["verts"]

        # Get influences from file
        skin_jnts = []

        for inf_id, inf_data in skin_data["influences"].items():
            inf_name = inf_data["name"]
            inf_short_name = inf_name.split("|")[-1]
            inf = self._find_influence_by_name(inf_name)

            if inf is None:
                if not create_missing_infs:
                    raise RuntimeError("Missing influence '{}'".format(inf_short_name))

                # Create new joint if influence is missing
                inf = cmds.createNode("joint", name=inf_short_name, skipSelect=True)
                cmds.xform(inf, ws=True, m=inf_data["world_matrix"])
                OpenMaya.MGlobal.displayWarning("Created '{}' because it was missing.".format(inf_short_name))

            skin_jnts.append(inf)

        if vert_filter:
            # Add any missing influences onto existing skin so that we can maintain weights.
            infs = self.get_all_infs()

            for inf in skin_jnts:
                if inf not in infs:
                    cmds.skinCluster(self.skin_cluster, e=True, lockWeights=True, weight=0, addInfluence=inf)
                    cmds.setAttr("{}.lockInfluenceWeights".format(inf), False)
        else:
            # Create new skin cluster with influences.
            if self.skin_cluster and cmds.objExists(self.skin_cluster):
                cmds.delete(self.skin_cluster)

            self.skin_cluster = utils.build_skin_cluster(
                self.name, skin_jnts,
                max_infs=skin_data["skin_cluster"]["max_influences"],
                skin_method=skin_data["skin_cluster"]["skinning_method"],
                dqs_support_non_rigid=skin_data["skin_cluster"]["dqs_support_non_rigid"],
                name=skin_data["skin_cluster"]["name"])

        # Define all verts to apply weights to.
        vert_indexes = [
            vert_index
            for vert_index in weights_data
            if not vert_filter or vert_index in vert_filter
        ]

        self.skin_data.data = weights_data
        self.collect_influence_colors()
        self.infs = self.get_all_infs()
        self.apply_current_skin_weights(vert_indexes, display_progress=True)

        return True

    def export_skin(self, file_path=None):
        """
        Exports skin weights to a file.

        Args:
            file_path(string): An absolute path to save weights to.
        """
        if not self.is_valid():
            raise RuntimeError("Need to pick a skinned object first.")

        if not self.has_valid_skin():
            raise RuntimeError("Picked object needs to have a skin cluster to export.")

        if file_path is None:
            file_path = self._launch_file_picker(0, "Export skin", file_name=self.name.split("|")[-1], ok_caption="Export")
            if not file_path:
                return

        skin_data = self.serialize()

        output_dir = os.path.dirname(file_path)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        with open(file_path, "wb") as f:
            f.write(cPickle.dumps(skin_data))

        return file_path

    @classmethod
    def export_all_skins(cls, delete_skin_cluster, export_folder=None):
        """
        Fetches all skinClusters in the scene and exports them all to a specified folder.

        Args:
            delete_skin_cluster(bool): If enabled, deletes the skinCluster after it's exported.
            export_folder(str): An absolute path to an existing folder to export the skins to. If None, a file picker will launch.
        """
        if export_folder is None:
            export_folder = cls._launch_file_picker(3, "Pick a folder to export all skinClusters to", ok_caption="Export")
            if not export_folder:
                return

        skin_clusters = cmds.ls(type="skinCluster")
        if not skin_clusters:
            OpenMaya.MGlobal.displayWarning("There are no skinClusters in the scene to export.")
            return

        for skin_cluster in skin_clusters:
            meshes = cmds.ls(cmds.listHistory(skin_cluster) or [], type="mesh")
            if not meshes:
                continue

            transform = cmds.listRelatives(meshes[0], parent=True)[0]
            export_path = "{}/{}.skin".format(export_folder, transform)
            skinned_obj = cls.create(transform)
            skinned_obj.export_skin(export_path)
            if delete_skin_cluster:
                cmds.delete(transform, ch=True)

    @classmethod
    def import_all_skins(cls, world_space, create_missing_infs, import_folder=None):
        """
        Fetches all skin files from the supplied folder and tries to import them all into the scene.
        It tries to load by name using the skin's file name.

        Args:
            world_space(bool): False=loads by point order, True=loads by world positions
            create_missing_infs(bool): Create any missing influences so the skin can still import.
            import_folder(string): An absolute path to a folder that contains skin files.
        """
        if import_folder is None:
            import_folder = cls._launch_file_picker(3, "Pick a folder with skin files to import them", ok_caption="Import")
            if not import_folder:
                return

        skin_files = glob.glob("{}/*.skin".format(import_folder))
        if not skin_files:
            OpenMaya.MGlobal.displayWarning("The folder contains no skin files to import with.")
            return

        for skin_path in skin_files:
            transform = os.path.basename(skin_path).split(".")[0]
            if not cmds.objExists(transform):
                OpenMaya.MGlobal.displayWarning("Unable to find the object to import weights onto: `{0}`".format(transform))
                continue

            skinned_obj = SkinnedObj.create(transform)
            skinned_obj.import_skin(file_path=skin_path, world_space=world_space, create_missing_infs=create_missing_infs)

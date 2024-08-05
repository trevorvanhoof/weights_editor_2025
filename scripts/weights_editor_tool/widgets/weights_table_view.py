from maya import cmds

from weights_editor_tool import weights_editor_utils as utils
from weights_editor_tool.widgets import abstract_weights_view
from weights_editor_tool.widgets.widgets_utils import *


class TableView(abstract_weights_view.AbstractWeightsView):

    update_ended = Signal(bool)

    def __init__(self, editor_inst):
        super(TableView, self).__init__(Qt.Horizontal, editor_inst)

        self._selected_rows = set()
        self._header.setSectionResizeMode(QHeaderView.ResizeToContents)

        self._sort_weights_vert_order_action = QAction(self)
        self._sort_weights_vert_order_action.setText("Sort by weights (vertex order)")
        self._sort_weights_vert_order_action.triggered.connect(self._sort_vert_order_on_triggered)

        self._header_context_menu.addAction(self._sort_weights_vert_order_action)

        table_model = TableModel(editor_inst, parent=self)
        self._set_model(table_model)

    def selectionChanged(self, selected, deselected):
        QTableView.selectionChanged(self, selected, deselected)
        self._cell_selection_on_changed()

    def closeEditor(self, editor, hint):
        """
        Enables multiple cells to be set.
        """
        is_cancelled = (hint == QAbstractItemDelegate.RevertModelCache)
        
        if not is_cancelled:
            for index in self.selectedIndexes():
                if index == self.currentIndex():
                    continue
                
                self.model().setData(index, None, Qt.EditRole)
        
        QTableView.closeEditor(self, editor, hint)
        
        if self.model().input_value is not None:
            self.model().input_value = None
            
            vert_indexes = list(set(
                self.model().get_vert_index(index.row())
                for index in self.selectedIndexes()))
            
            self._editor_inst.add_undo_command(
                "Set skin weights",
                self._editor_inst.obj.name,
                self._old_skin_data,
                self._editor_inst.obj.skin_data.copy(),
                vert_indexes,
                self.save_table_selection())
        
        self._old_skin_data = None

    def _sort_ascending_on_triggered(self):
        self._reorder_rows(self._header.last_index, Qt.DescendingOrder)

    def _sort_descending_on_triggered(self):
        self._reorder_rows(self._header.last_index, Qt.AscendingOrder)

    def _sort_vert_order_on_triggered(self):
        self._reorder_rows(self._header.last_index, None)

    def _cell_selection_on_changed(self):
        """
        Selects vertexes based on what was selected on the table.
        """
        if self._editor_inst.ignore_cell_selection_event or \
                not self._editor_inst.auto_select_vertex_action.isChecked():
            return

        rows = set(
            index.row()
            for index in self._get_selected_indexes()
        )

        if rows == self._selected_rows:
            return

        self._selected_rows = rows

        if self._editor_inst.obj.is_valid():
            component = "vtx"
            if utils.is_curve(self._editor_inst.obj.name):
                component = "cv"

            vertex_list = [
                "{0}.{1}[{2}]".format(self._editor_inst.obj.name, component, self._editor_inst.vert_indexes[row])
                for row in rows
            ]
        else:
            vertex_list = []

        self._editor_inst.block_selection_cb = True
        cmds.select(vertex_list)
        self._editor_inst.block_selection_cb = False

    def _reorder_rows(self, column, order):
        """
        Re-orders and displays rows by weight values.

        Args:
            column(int): The influence to compare weights with.
            order(Qt.SortOrder): The direction to sort the weights by.
                                        If None, re-orders based on vertex index.
        """
        self.begin_update()
        selection_data = self.save_table_selection()

        inf = self.table_model.display_infs[column]

        if order is None:
            self._editor_inst.vert_indexes = sorted(self._editor_inst.vert_indexes)
        else:
            self._editor_inst.vert_indexes = sorted(
                self._editor_inst.vert_indexes,
                key=lambda x: self._editor_inst.obj.skin_data[x]["weights"].get(inf) or 0.0,
                reverse=order)

        self.end_update()
        self.load_table_selection(selection_data)

    def color_headers(self):
        count = self.table_model.columnCount(self)
        super(TableView, self).color_headers(count)

    def select_items_by_inf(self, inf):
        if inf and inf in self.table_model.display_infs:
            column = self.table_model.display_infs.index(inf)
            selection_model = self.selectionModel()
            index = self.model().createIndex(0, column)
            flags = QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Columns
            selection_model.select(index, flags)
        else:
            self.clearSelection()

    def get_selected_verts_and_infs(self):
        indexes = self._get_selected_indexes()
        if not indexes:
            return []

        verts_and_infs = []

        for index in indexes:
            row = index.row()
            column = index.column()
            if column >= len(self.table_model.display_infs):
                continue

            vert_index = self._editor_inst.vert_indexes[row]
            inf = self.table_model.display_infs[column]
            verts_and_infs.append((vert_index, inf))

        return verts_and_infs

    def save_table_selection(self):
        """
        Saves table's selection to a data set.

        Returns:
            A dictionary representing the selection.
            {inf_name:[vert_index, ..]}
        """
        selection_data = {}

        for index in self.selectedIndexes():
            if not index.isValid():
                continue

            if index.column() > len(self.table_model.display_infs) - 1:
                continue

            inf = self.table_model.display_infs[index.column()]
            if inf not in selection_data:
                selection_data[inf] = []

            if index.row() > len(self._editor_inst.vert_indexes):
                continue

            vert_index = self._editor_inst.vert_indexes[index.row()]
            selection_data[inf].append(vert_index)

        return selection_data

    def load_table_selection(self, selection_data):
        """
        Attempts to load selection by supplied data set.

        Args:
            selection_data(dict): See save method for data's structure.
        """
        self.clearSelection()

        if not selection_data:
            return

        selection_model = self.selectionModel()
        item_selection = QItemSelection()

        for inf, vert_indexes in selection_data.items():
            if inf not in self.table_model.display_infs:
                continue

            column = self.table_model.display_infs.index(inf)

            for vert_index in vert_indexes:
                if vert_index not in self._editor_inst.vert_indexes:
                    continue

                row = self._editor_inst.vert_indexes.index(vert_index)
                index = self.model().index(row, column)
                item_selection.append(QItemSelectionRange(index, index))

        selection_model.select(item_selection, QItemSelectionModel.Select)

    def fit_headers_to_contents(self):
        for i in range(self.horizontalHeader().count()):
            self.resizeColumnToContents(i)

    def end_update(self):
        super(TableView, self).end_update()
        over_limit = len(self._editor_inst.vert_indexes) > self.table_model.max_display_count
        self.update_ended.emit(over_limit)


class TableModel(abstract_weights_view.AbstractModel):
    
    def __init__(self, editor_inst, parent=None):
        super(TableModel, self).__init__(editor_inst, parent)
        self.max_display_count = 5000
    
    def rowCount(self, parent):
        return min(len(self._editor_inst.vert_indexes), self.max_display_count)
    
    def columnCount(self, parent):
        if self._editor_inst.vert_indexes:
            return len(self.display_infs)
        else:
            return 0

    def data(self, index, role):
        if not index.isValid():
            return

        roles = [Qt.ForegroundRole, Qt.DisplayRole, Qt.EditRole]

        if role in roles:
            inf = self.get_inf(index.column())
            value = self._get_value_by_index(index)
            
            if role == Qt.ForegroundRole:
                inf_index = self._editor_inst.obj.infs.index(inf)
                is_locked = self._editor_inst.locks[inf_index]
                if is_locked:
                    return self._locked_text

                if value != 0 and value < 0.001:
                    return self._low_weight_text
                elif value == 0:
                    return self._zero_weight_text
                elif value >= 0.999:
                    return self._full_weight_text
            else:
                if value != 0 and value < 0.001:
                    return "< 0.001"
                return "{0:.3f}".format(value)
    
    def setData(self, index, value, role):
        """
        Qt doesn't handle multiple cell edits very well.
        This is the only place we can get the user's input, so first we check if it's valid first.
        If not, all other cells will be ignored.
        """
        if not index.isValid():
            return False
        
        if role != Qt.EditRole:
            return False
        
        # Triggers if first cell wasn't valid
        if value is None and self.input_value is None:
            return False

        if self.input_value is None:
            if not value.replace(".", "").isdigit():
                return False
            
            value = float(value)
            
            if not (value >= 0 and value <= 1):
                return False

            # Skip if the values are the same.
            # Necessary since left-clicking out of cell won't cancel.
            old_value = self._get_value_by_index(index)
            old_value_str = "{0:.3f}".format(old_value)
            value_str = "{0:.3f}".format(value)
            if value_str == old_value_str:
                return False

            self.input_value = value
        else:
            value = self.input_value

        # Distribute the weights.
        inf = self.get_inf(index.column())
        vert_index = self.get_vert_index(index.row())
        self._editor_inst.obj.skin_data.update_weight_value(
            vert_index, inf, value)
        
        return True
    
    def headerData(self, column, orientation, role):
        """
        Deterimines the header's labels and style.
        """
        if role == Qt.ForegroundRole:
            # Color locks
            if orientation == Qt.Horizontal:
                inf_name = self.display_infs[column]
                
                if inf_name in self._editor_inst.obj.infs:
                    inf_index = self._editor_inst.obj.infs.index(inf_name)
                    
                    is_locked = self._editor_inst.locks[inf_index]
                    if is_locked:
                        return self._header_locked_text
        elif role == Qt.BackgroundColorRole:
            # Color background
            if orientation == Qt.Horizontal:
                # Use softimage colors
                if self.header_colors:
                    color = self.header_colors[column]
                    if color is not None:
                        return color
                else:
                    # Color selected inf
                    if self._editor_inst.color_inf is not None:
                        if self._editor_inst.color_inf == self.get_inf(column):
                            return self._header_active_inf_back_color
        elif role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                # Show top labels
                if self.display_infs and column < len(self.display_infs):
                    inf = self.display_infs[column]
                    if self.hide_long_names:
                        return inf.split("|")[-1]
                    return inf
            else:
                # Show side labels
                if self._editor_inst.vert_indexes and column < len(self._editor_inst.vert_indexes):
                    return "vtx[{0}]".format(self._editor_inst.vert_indexes[column])
        elif role == Qt.ToolTipRole:
            if orientation == Qt.Horizontal:
                if self.display_infs and column < len(self.display_infs):
                    return self.display_infs[column]

    def _get_value_by_index(self, index):
        inf = self.get_inf(index.column())
        vert_index = self.get_vert_index(index.row())
        return self._editor_inst.obj.skin_data[vert_index]["weights"].get(inf) or 0

    def get_vert_index(self, row):
        return self._editor_inst.vert_indexes[row]

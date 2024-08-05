from weights_editor_tool.widgets import abstract_weights_view
from weights_editor_tool.widgets.widgets_utils import *


class ListView(abstract_weights_view.AbstractWeightsView):

    def __init__(self, editor_inst):
        super(ListView, self).__init__(Qt.Vertical, editor_inst)

        self._sort_inf_name_action = QAction(self)
        self._sort_inf_name_action.setText("Sort by inf name")
        self._sort_inf_name_action.triggered.connect(self._sort_inf_name_on_triggered)

        self._header_context_menu.addAction(self._sort_inf_name_action)

        table_model = ListModel(editor_inst, parent=self)
        self._set_model(table_model)

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
            
            self._editor_inst.add_undo_command(
                "Set skin weights",
                self._editor_inst.obj.name,
                self._old_skin_data,
                self._editor_inst.obj.skin_data.copy(),
                self._editor_inst.vert_indexes,
                self.save_table_selection())
        
        self._old_skin_data = None

    def _sort_ascending_on_triggered(self):
        self._reorder_by_values(Qt.DescendingOrder)

    def _sort_descending_on_triggered(self):
        self._reorder_by_values(Qt.AscendingOrder)

    def _sort_inf_name_on_triggered(self):
        self._reorder_by_name()

    def _reorder_by_name(self, order=Qt.AscendingOrder):
        self.begin_update()
        selection_data = self.save_table_selection()

        self.table_model.display_infs.sort(reverse=order)

        self.end_update()
        self.load_table_selection(selection_data)

    def _reorder_by_values(self, order):
        self.begin_update()
        selection_data = self.save_table_selection()

        self.table_model.display_infs = sorted(
            self.table_model.display_infs,
            key=lambda x: self.table_model.get_average_weight(x) or 0.0,
            reverse=order)

        self.end_update()
        self.load_table_selection(selection_data)

    def end_update(self):
        self.table_model.average_weights = {}
        super(ListView, self).end_update()

    def color_headers(self):
        count = self.table_model.rowCount(self)
        super(ListView, self).color_headers(count)

    def select_items_by_inf(self, inf):
        if inf and inf in self.table_model.display_infs:
            row = self.table_model.display_infs.index(inf)
            selection_model = self.selectionModel()
            index = self.model().createIndex(row, 0)
            flags = QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
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
            if row >= len(self.table_model.display_infs):
                continue

            for vert_index in self._editor_inst.vert_indexes:
                inf = self.table_model.display_infs[row]
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

        verts_and_infs = self.get_selected_verts_and_infs()
        for vert_index, inf in verts_and_infs:
            if inf not in selection_data:
                selection_data[inf] = []
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

            row = self.table_model.display_infs.index(inf)
            index = self.model().index(row, 0)
            item_selection.append(QItemSelectionRange(index, index))

        selection_model.select(item_selection, QItemSelectionModel.Select)

    def fit_headers_to_contents(self):
        width = 0
        infs = self.display_infs()

        if infs and self._editor_inst.vert_indexes:
            if self.table_model.hide_long_names:
                infs = [inf.split("|")[-1] for inf in infs]

            font_metrics = self._editor_inst.fontMetrics()
            padding = 10

            width = sorted([
                font_metrics.width(inf)
                for inf in infs])[-1] + padding

        self.verticalHeader().size = QSize(width, 0)


class ListModel(abstract_weights_view.AbstractModel):
    
    def __init__(self, editor_inst, parent=None):
        super(ListModel, self).__init__(editor_inst, parent)

        self.average_weights = {}
    
    def rowCount(self, parent):
        if self._editor_inst.vert_indexes:
            return len(self.display_infs)
        else:
            return 0
    
    def columnCount(self, parent):
        if self.display_infs and self._editor_inst.vert_indexes:
            return 1
        else:
            return 0

    def data(self, index, role):
        if not index.isValid():
            return

        roles = [Qt.ForegroundRole, Qt.DisplayRole, Qt.EditRole]

        if role in roles:
            inf = self.get_inf(index.row())
            value = self.get_average_weight(inf)
            
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
            if not value >= 0 and value <= 1:
                return False

            self.input_value = value
        else:
            value = self.input_value

        # Distribute the weights.
        inf = self.get_inf(index.row())

        for vert_index in self._editor_inst.vert_indexes:
            self._editor_inst.obj.skin_data.update_weight_value(
                vert_index, inf, value)

        return True
    
    def headerData(self, index, orientation, role):
        """
        Deterimines the header's labels and style.
        """
        if role == Qt.ForegroundRole:
            # Color locks
            if orientation == Qt.Vertical:
                inf_name = self.display_infs[index]
                
                if inf_name in self._editor_inst.obj.infs:
                    inf_index = self._editor_inst.obj.infs.index(inf_name)
                    
                    is_locked = self._editor_inst.locks[inf_index]
                    if is_locked:
                        return self._header_locked_text
        elif role == Qt.BackgroundColorRole:
            # Color background
            if orientation == Qt.Vertical:
                # Use softimage colors
                if self.header_colors:
                    color = self.header_colors[index]
                    if color is not None:
                        return color
                else:
                    # Color selected inf
                    if self._editor_inst.color_inf is not None:
                        if self._editor_inst.color_inf == self.get_inf(index):
                            return self._header_active_inf_back_color
        elif role == Qt.DisplayRole:
            if orientation == Qt.Vertical:
                # Show top labels
                if self.display_infs and index < len(self.display_infs):
                    inf = self.display_infs[index]
                    if self.hide_long_names:
                        return inf.split("|")[-1]
                    return inf
            else:
                return "Average values"
        elif role == Qt.ToolTipRole:
            if orientation == Qt.Vertical:
                if self.display_infs and index < len(self.display_infs):
                    return self.display_infs[index]

    def get_average_weight(self, inf):
        if not self._editor_inst.vert_indexes:
            return 0

        if inf not in self.average_weights:
            values = [
                self._editor_inst.obj.skin_data[vert_index]["weights"].get(inf) or 0
                for vert_index in self._editor_inst.vert_indexes
            ]

            self.average_weights[inf] = sum(values) / len(self._editor_inst.vert_indexes)

        return self.average_weights[inf]

from maya import cmds

from weights_editor_tool import weights_editor_utils as utils
from weights_editor_tool.enums import ColorTheme
from weights_editor_tool.widgets import custom_header_view
from weights_editor_tool.widgets.widgets_utils import *


class AbstractWeightsView(QTableView):

    key_pressed = Signal(QKeyEvent)
    header_middle_clicked = Signal(str)
    display_inf_triggered = Signal(str)
    select_inf_verts_triggered = Signal(str)

    def __init__(self, header_orientation, editor_inst):
        super(AbstractWeightsView, self).__init__(editor_inst)

        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setGridStyle(Qt.DashLine)

        system_font = QApplication.font()

        self._orientation = header_orientation
        self._font = QFont(system_font.family(), system_font.pixelSize())
        self._editor_inst = editor_inst
        self._old_skin_data = None  # Need to store this to work with undo/redo.
        self.table_model = None

        self._header = None

        if header_orientation == Qt.Horizontal:
            self._header = custom_header_view.CustomHeaderView(header_orientation, parent=self)
        else:
            self._header = custom_header_view.VerticalHeaderView(header_orientation, parent=self)

        self._header.setContextMenuPolicy(Qt.CustomContextMenu)
        self._header.customContextMenuRequested.connect(self._header_on_context_trigger)
        self._header.header_left_clicked.connect(self._header_on_left_clicked)
        self._header.header_middle_clicked.connect(self._header_on_middle_clicked)

        if header_orientation == Qt.Horizontal:
            self.setHorizontalHeader(self._header)
        else:
            self.setVerticalHeader(self._header)

        self._lock_inf_action = QAction(self)
        self._lock_inf_action.setText("Lock influence")
        self._lock_inf_action.triggered.connect(self._lock_inf_on_triggered)

        self._unlock_inf_action = QAction(self)
        self._unlock_inf_action.setText("Unlock influence")
        self._unlock_inf_action.triggered.connect(self._unlock_inf_on_triggered)

        self._display_inf_action = QAction(self)
        self._display_inf_action.setText("Display influence (middle-click)")
        self._display_inf_action.triggered.connect(self._display_inf_on_triggered)

        self._select_inf_verts_action = QAction(self)
        self._select_inf_verts_action.setText("Select vertexes effected by influence")
        self._select_inf_verts_action.triggered.connect(self._select_inf_verts_on_triggered)

        self._select_inf_action = QAction(self)
        self._select_inf_action.setText("Select influence")
        self._select_inf_action.triggered.connect(self._select_inf_on_triggered)

        self._sort_weights_ascending_action = QAction(self)
        self._sort_weights_ascending_action.setText("Sort by weights (ascending)")
        self._sort_weights_ascending_action.triggered.connect(self._sort_ascending_on_triggered)

        self._sort_weights_descending_action = QAction(self)
        self._sort_weights_descending_action.setText("Sort by weights (descending)")
        self._sort_weights_descending_action.triggered.connect(self._sort_descending_on_triggered)

        self._header_context_menu = QMenu(parent=self)
        self._header_context_menu.addAction(self._display_inf_action)
        self._header_context_menu.addSeparator()
        self._header_context_menu.addAction(self._lock_inf_action)
        self._header_context_menu.addAction(self._unlock_inf_action)
        self._header_context_menu.addSeparator()
        self._header_context_menu.addAction(self._select_inf_verts_action)
        self._header_context_menu.addAction(self._select_inf_action)
        self._header_context_menu.addSeparator()
        self._header_context_menu.addAction(self._sort_weights_ascending_action)
        self._header_context_menu.addAction(self._sort_weights_descending_action)

    def _sort_ascending_on_triggered(self):
        raise NotImplementedError

    def _sort_descending_on_triggered(self):
        raise NotImplementedError

    def select_items_by_inf(self):
        raise NotImplementedError

    def get_selected_verts_and_infs(self):
        raise NotImplementedError

    def save_table_selection(self):
        raise NotImplementedError

    def load_table_selection(self, selection_data):
        raise NotImplementedError

    def fit_headers_to_contents(self):
        raise NotImplementedError

    def paintEvent(self, paint_event):
        """
        Shows tooltip when table is empty.
        """
        if self.model().rowCount(self) == 0:
            if not self._editor_inst.obj.is_valid():
                msg = ("Select a skinned object and push\n"
                       "the button on top edit its weights.")
                img = utils.load_pixmap("table_view/select_skin.png")
            elif not self._editor_inst.obj.has_valid_skin():
                msg = "Unable to detect a skinCluster on this object."
                img = utils.load_pixmap("table_view/sad.png")
            else:
                msg = "Select the object's components to edit it."
                img = utils.load_pixmap("table_view/select_points.png")
            
            qp = QPainter(self.viewport())
            if not qp.isActive():
                qp.begin(self)

            if img is not None:
                qp.drawPixmap(
                    self.width() / 2 - img.width() / 2,
                    self.height() / 2 - img.height(),
                    img)

            rect = paint_event.rect()
            rect.setTop(self.height() / 2)

            qp.setPen(QColor(255, 255, 255))
            qp.setFont(self._font)
            qp.drawText(rect, Qt.AlignHCenter | Qt.AlignTop, msg)
            qp.end()
        
        QTableView.paintEvent(self, paint_event)

    def keyPressEvent(self, event):
        self.key_pressed.emit(event)
    
    def mousePressEvent(self, event):
        QTableView.mousePressEvent(self, event)

        # Begins edit on current cell.
        if event.button() == Qt.MouseButton.RightButton:
            # Save this prior to any changes.
            self._old_skin_data = self._editor_inst.obj.skin_data.copy()
            self.edit(self.currentIndex())

    def _get_last_clicked_inf(self):
        return self.table_model.display_infs[self._header.last_index]

    def _header_on_context_trigger(self, point):
        self._header_context_menu.exec_(self.mapToGlobal(point))

    def _header_on_left_clicked(self, index):
        self.selectColumn(index)

    def _header_on_middle_clicked(self, index):
        inf = self.table_model.display_infs[index]
        self.header_middle_clicked.emit(inf)

    def _display_inf_on_triggered(self):
        self.display_inf_triggered.emit(self._get_last_clicked_inf())

    def _lock_inf_on_triggered(self):
        self._editor_inst.toggle_inf_locks([self._get_last_clicked_inf()], True)

    def _unlock_inf_on_triggered(self):
        self._editor_inst.toggle_inf_locks([self._get_last_clicked_inf()], False)

    def _select_inf_verts_on_triggered(self):
        self.select_inf_verts_triggered.emit(self._get_last_clicked_inf())

    def _select_inf_on_triggered(self):
        inf = self._get_last_clicked_inf()
        if cmds.objExists(inf):
            cmds.select(inf)

    def _set_model(self, abstract_model):
        self.table_model = abstract_model
        self.setModel(self.table_model)

    def _reset_color_headers(self):
        self.table_model.header_colors = []

    def _get_selected_indexes(self):
        return [
            index
            for index in self.selectedIndexes()
            if index.isValid()
        ]

    def display_infs(self):
        return self.table_model.display_infs

    def set_display_infs(self, new_infs):
        self.table_model.display_infs = new_infs

    def begin_update(self):
        self.table_model.layoutAboutToBeChanged.emit()

    def end_update(self):
        self.table_model.layoutChanged.emit()

    def emit_header_data_changed(self):
        inf_count = len(self.table_model.display_infs)

        if self._orientation == Qt.Horizontal:
            self.table_model.headerDataChanged.emit(Qt.Horizontal, 0, inf_count)
        else:
            self.table_model.headerDataChanged.emit(Qt.Vertical, 0, inf_count)

    def color_headers(self, count):
        """
        Resets the colors on the top headers.
        An active influence will be colored as blue.
        When using the Softimage theme, each header will be the color if its influence.
        """
        self._reset_color_headers()

        if self._editor_inst.color_style == ColorTheme.Softimage:
            for index in range(count):
                header_name = self.table_model.get_inf(index)
                rgb = self._editor_inst.obj.inf_colors.get(header_name)

                color = None
                if rgb is not None:
                    color = QColor.fromRgbF(*rgb)
                self.table_model.header_colors.append(color)

    def toggle_long_names(self, hidden):
        self.begin_update()
        try:
            self.table_model.hide_long_names = hidden
        finally:
            self.end_update()
            self.fit_headers_to_contents()


class AbstractModel(QAbstractTableModel):
    
    def __init__(self, editor_inst, parent=None):
        super(AbstractModel, self).__init__(parent)
        
        self._editor_inst = editor_inst
        self._locked_text = QColor(100, 100, 100)
        self._full_weight_text = QColor(Qt.white)
        self._low_weight_text = QColor(Qt.yellow)
        self._zero_weight_text = QColor(255, 50, 50)
        self._header_locked_text = QColor(Qt.black)
        self._header_active_inf_back_color = QColor(0, 120, 180)

        self.header_colors = []
        self.display_infs = []
        self.input_value = None  # Used to properly set multiple cells
        self.hide_long_names = True

    def rowCount(self, parent):
        raise NotImplementedError

    def columnCount(self, parent):
        raise NotImplementedError

    def data(self, index, role):
        raise NotImplementedError

    def setData(self, index, value, role):
        raise NotImplementedError

    def headerData(self, column, orientation, role):
        raise NotImplementedError

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def get_inf(self, index):
        return self.display_infs[index]

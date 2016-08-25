# standard libraries
import unittest

# third party libraries
import numpy

# local libraries
from nion.data import DataAndMetadata
from nion.swift import Application
from nion.swift import DocumentController
from nion.swift import Panel
from nion.swift.model import DataItem
from nion.swift.model import DocumentModel
from nion.swift.model import Graphics
from nion.ui import TestUI


class TestInfoPanelClass(unittest.TestCase):

    def setUp(self):
        self.app = Application.Application(TestUI.UserInterface(), set_global=False)

    def tearDown(self):
        pass

    def test_cursor_over_1d_data_displays_without_exception(self):
        document_model = DocumentModel.DocumentModel()
        document_controller = DocumentController.DocumentController(self.app.ui, document_model, workspace_id="library")
        display_panel = document_controller.selected_display_panel
        data_item = DataItem.DataItem(numpy.zeros((1000, )))
        document_model.append_data_item(data_item)
        display_panel.set_displayed_data_item(data_item)
        header_height = Panel.HeaderCanvasItem().header_height
        display_panel.canvas_item.root_container.canvas_widget.on_size_changed(1000, 1000 + header_height)
        display_panel.display_canvas_item.mouse_entered()
        display_panel.display_canvas_item.mouse_position_changed(500, 500, Graphics.NullModifiers())
        display_panel.display_canvas_item.mouse_exited()
        document_controller.close()

    def test_cursor_over_1d_data_displays_without_exception_when_not_displaying_calibration(self):
        document_model = DocumentModel.DocumentModel()
        document_controller = DocumentController.DocumentController(self.app.ui, document_model, workspace_id="library")
        display_panel = document_controller.selected_display_panel
        data_item = DataItem.DataItem(numpy.zeros((1000, )))
        document_model.append_data_item(data_item)
        data_item.data_sources[0].displays[0].display_calibrated_values = False
        display_panel.set_displayed_data_item(data_item)
        header_height = Panel.HeaderCanvasItem().header_height
        display_panel.canvas_item.root_container.canvas_widget.on_size_changed(1000, 1000 + header_height)
        display_panel.display_canvas_item.mouse_entered()
        display_panel.display_canvas_item.mouse_position_changed(500, 500, Graphics.NullModifiers())
        display_panel.display_canvas_item.mouse_exited()
        document_controller.close()

    def test_cursor_over_1d_multiple_data_displays_without_exception(self):
        data_and_metadata = DataAndMetadata.new_data_and_metadata(numpy.zeros((4, 1000), numpy.float64), data_descriptor=DataAndMetadata.DataDescriptor(False, 1, 1))
        data_item = DataItem.new_data_item(data_and_metadata)
        display = DataItem.DisplaySpecifier.from_data_item(data_item).display
        p, v = display.get_value_and_position_text((500,))
        self.assertEqual(p, "500.0, 0.0")
        self.assertEqual(v, "0")

    def test_cursor_over_1d_sequence_data_displays_without_exception(self):
        data_and_metadata = DataAndMetadata.new_data_and_metadata(numpy.zeros((4, 1000), numpy.float64), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 1))
        data_item = DataItem.new_data_item(data_and_metadata)
        display = DataItem.DisplaySpecifier.from_data_item(data_item).display
        p, v = display.get_value_and_position_text((500,))
        self.assertEqual(p, "500.0, 0.0")
        self.assertEqual(v, "0")

    def test_cursor_over_1d_image_without_exception(self):
        data_item = DataItem.DataItem(numpy.zeros((50,)))
        display = DataItem.DisplaySpecifier.from_data_item(data_item).display
        p, v = display.get_value_and_position_text((25, ))
        self.assertEqual(p, "25.0")
        self.assertEqual(v, "0")

    def test_cursor_over_3d_data_displays_without_exception(self):
        document_model = DocumentModel.DocumentModel()
        document_controller = DocumentController.DocumentController(self.app.ui, document_model, workspace_id="library")
        display_panel = document_controller.selected_display_panel
        data_item = DataItem.DataItem(numpy.zeros((10, 10, 4)))
        document_model.append_data_item(data_item)
        display_panel.set_displayed_data_item(data_item)
        header_height = Panel.HeaderCanvasItem().header_height
        display_panel.canvas_item.root_container.canvas_widget.on_size_changed(1000, 1000 + header_height)
        display_panel.display_canvas_item.mouse_entered()
        display_panel.display_canvas_item.mouse_position_changed(500, 500, Graphics.NullModifiers())
        display_panel.display_canvas_item.mouse_exited()
        document_controller.close()

    def test_cursor_over_3d_data_displays_correct_ordering_of_indices(self):
        document_model = DocumentModel.DocumentModel()
        document_controller = DocumentController.DocumentController(self.app.ui, document_model, workspace_id="library")
        display_panel = document_controller.selected_display_panel
        data_item = DataItem.DataItem(numpy.ones((100, 100, 20)))
        document_model.append_data_item(data_item)
        display_panel.set_displayed_data_item(data_item)
        header_height = Panel.HeaderCanvasItem().header_height
        info_panel = document_controller.find_dock_widget("info-panel").panel
        display_panel.canvas_item.root_container.canvas_widget.on_size_changed(1000, 1000 + header_height)
        display_panel.display_canvas_item.mouse_entered()
        display_panel.display_canvas_item.mouse_position_changed(500, 500, Graphics.NullModifiers())
        document_controller.periodic()
        self.assertEqual(info_panel.label_row_1.text, "Position: 0.0, 50.0, 50.0")
        self.assertEqual(info_panel.label_row_2.text, "Value: 1")
        self.assertIsNone(info_panel.label_row_3.text, None)
        display_panel.display_canvas_item.mouse_exited()
        document_controller.close()

    def test_cursor_over_2d_data_sequence_displays_correct_ordering_of_indices(self):
        document_model = DocumentModel.DocumentModel()
        document_controller = DocumentController.DocumentController(self.app.ui, document_model, workspace_id="library")
        display_panel = document_controller.selected_display_panel
        data_and_metadata = DataAndMetadata.new_data_and_metadata(numpy.ones((20, 100, 100), numpy.float64), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
        data_item = DataItem.new_data_item(data_and_metadata)
        document_model.append_data_item(data_item)
        DataItem.DisplaySpecifier.from_data_item(data_item).display.sequence_index = 4
        display_panel.set_displayed_data_item(data_item)
        header_height = Panel.HeaderCanvasItem().header_height
        info_panel = document_controller.find_dock_widget("info-panel").panel
        display_panel.canvas_item.root_container.canvas_widget.on_size_changed(1000, 1000 + header_height)
        display_panel.display_canvas_item.mouse_entered()
        display_panel.display_canvas_item.mouse_position_changed(500, 500, Graphics.NullModifiers())
        document_controller.periodic()
        self.assertEqual(info_panel.label_row_1.text, "Position: 50.0, 50.0, 4.0")
        self.assertEqual(info_panel.label_row_2.text, "Value: 1")
        self.assertIsNone(info_panel.label_row_3.text, None)
        display_panel.display_canvas_item.mouse_exited()
        document_controller.close()

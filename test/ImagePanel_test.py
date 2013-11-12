# standard libraries
import logging
import unittest

# third party libraries
import numpy

# local libraries
from nion.swift import Application
from nion.swift import DataItem
from nion.swift import DocumentController
from nion.swift import DocumentModel
from nion.swift import Graphics
from nion.swift import ImagePanel
from nion.swift import Storage
from nion.swift import Test


class TestGraphicSelectionClass(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_selection_set(self):
        selection = ImagePanel.GraphicSelection()
        selection.set(0)
        selection.set(1)
        self.assertEqual(len(selection.indexes), 1)


class TestImagePanelClass(unittest.TestCase):

    def setUp(self):
        self.app = Application.Application(Test.UserInterface(), set_global=False)
        db_name = ":memory:"
        storage_writer = Storage.DbStorageWriter(db_name)
        storage_cache = Storage.DbStorageCache(db_name)
        document_model = DocumentModel.DocumentModel(storage_writer, storage_cache)
        self.document_controller = DocumentController.DocumentController(self.app.ui, document_model, workspace_id="library")
        self.document_controller.document_model.create_default_data_groups()
        default_data_group = self.document_controller.document_model.data_groups[0]
        self.image_panel = self.document_controller.selected_image_panel
        self.image_panel.image_canvas_item.update_layout((0, 0), (1000, 1000))
        self.data_item = self.document_controller.document_model.set_data_by_key("test", numpy.zeros((1000, 1000)))
        self.image_panel.data_panel_selection = DataItem.DataItemSpecifier(default_data_group, self.data_item)

    def tearDown(self):
        self.image_panel.close()
        self.document_controller.close()

    def simulate_click(self, p, modifiers=None):
        modifiers = Test.KeyboardModifiers() if not modifiers else modifiers
        self.image_panel.image_canvas_item.mouse_pressed(p[1], p[0], modifiers)
        self.image_panel.image_canvas_item.mouse_released(p[1], p[0], modifiers)

    def simulate_drag(self, p1, p2, modifiers=None):
        modifiers = Test.KeyboardModifiers() if not modifiers else modifiers
        self.image_panel.image_canvas_item.mouse_pressed(p1[1], p1[0], modifiers)
        self.image_panel.image_canvas_item.mouse_position_changed(p1[1], p1[0], modifiers)
        midp = Graphics.midpoint(p1, p2)
        self.image_panel.image_canvas_item.mouse_position_changed(midp[1], midp[0], modifiers)
        self.image_panel.image_canvas_item.mouse_position_changed(p2[1], p2[0], modifiers)
        self.image_panel.image_canvas_item.mouse_released(p2[1], p2[0], modifiers)

    # user deletes data item that is displayed. make sure we remove the display.
    def test_disappearing_data(self):
        self.assertEqual(self.image_panel.data_panel_selection.data_group, self.document_controller.document_model.data_groups[0])
        self.assertEqual(self.image_panel.data_panel_selection.data_item, self.document_controller.document_model.data_groups[0].data_items[0])
        self.document_controller.processing_invert()
        data_group = self.document_controller.document_model.data_groups[0]
        data_item_container = data_group.data_items[0]
        data_item = data_item_container.data_items[0]
        self.assertEqual(self.image_panel.data_panel_selection.data_group, data_group)
        self.assertEqual(self.image_panel.data_panel_selection.data_item_container, data_item_container)
        self.assertEqual(self.image_panel.data_panel_selection.data_item, data_item)
        data_item_container.data_items.remove(data_item)
        self.assertEqual(self.image_panel.data_panel_selection.data_group, data_group)
        self.assertIsNone(self.image_panel.data_panel_selection.data_item_container)
        self.assertIsNone(self.image_panel.data_panel_selection.data_item)

    def test_select_multiple(self):
        # add line (0.2, 0.2), (0.8, 0.8) and ellipse ((0.25, 0.25), (0.5, 0.5)).
        self.document_controller.add_line_graphic()
        self.document_controller.add_ellipse_graphic()
        # click outside so nothing is selected
        self.simulate_click((0, 0))
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 0)
        # select the ellipse
        self.simulate_click((725, 500))
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertTrue(1 in self.image_panel.graphic_selection.indexes)
        # select the line
        self.simulate_click((200, 200))
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertTrue(0 in self.image_panel.graphic_selection.indexes)
        # add the ellipse to the selection. click inside the right side.
        self.simulate_click((725, 500), Test.KeyboardModifiers(shift=True))
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 2)
        self.assertTrue(0 in self.image_panel.graphic_selection.indexes)
        # remove the ellipse from the selection. click inside the right side.
        self.simulate_click((725, 500), Test.KeyboardModifiers(shift=True))
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertTrue(0 in self.image_panel.graphic_selection.indexes)

    def assertClosePoint(self, p1, p2, e=0.00001):
        self.assertTrue(Graphics.distance(p1, p2) < e)

    def assertCloseRectangle(self, r1, r2, e=0.00001):
        self.assertTrue(Graphics.distance(r1[0], r2[0]) < e and Graphics.distance(r1[1], r2[1]) < e)

    def test_drag_multiple(self):
        # add line (0.2, 0.2), (0.8, 0.8) and ellipse ((0.25, 0.25), (0.5, 0.5)).
        self.document_controller.add_line_graphic()
        self.document_controller.add_ellipse_graphic()
        # make sure items are in the right place
        self.assertClosePoint(self.data_item.graphics[0].start, (0.2, 0.2))
        self.assertClosePoint(self.data_item.graphics[0].end, (0.8, 0.8))
        self.assertCloseRectangle(self.data_item.graphics[1].bounds, ((0.25, 0.25), (0.5, 0.5)))
        # select both
        self.image_panel.graphic_selection.set(0)
        self.image_panel.graphic_selection.add(1)
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 2)
        # drag by (0.1, 0.2)
        self.simulate_drag((500,500), (600,700))
        self.assertCloseRectangle(self.data_item.graphics[1].bounds, ((0.35, 0.45), (0.5, 0.5)))
        self.assertClosePoint(self.data_item.graphics[0].start, (0.3, 0.4))
        self.assertClosePoint(self.data_item.graphics[0].end, (0.9, 1.0))
        # drag on endpoint (0.3, 0.4) make sure it drags all
        self.simulate_drag((300,400), (200,200))
        self.assertClosePoint(self.data_item.graphics[0].start, (0.2, 0.2))
        self.assertClosePoint(self.data_item.graphics[0].end, (0.8, 0.8))
        self.assertCloseRectangle(self.data_item.graphics[1].bounds, ((0.25, 0.25), (0.5, 0.5)))
        # now select just the line, drag middle of circle. should only drag circle.
        self.image_panel.graphic_selection.set(0)
        self.simulate_drag((700,500), (800,500))
        self.assertClosePoint(self.data_item.graphics[0].start, (0.2, 0.2))
        self.assertClosePoint(self.data_item.graphics[0].end, (0.8, 0.8))
        self.assertCloseRectangle(self.data_item.graphics[1].bounds, ((0.35, 0.25), (0.5, 0.5)))

    def test_drag_line_part(self):
        # add line (0.2, 0.2), (0.8, 0.8)
        self.document_controller.add_line_graphic()
        # make sure items it is in the right place
        self.assertClosePoint(self.data_item.graphics[0].start, (0.2, 0.2))
        self.assertClosePoint(self.data_item.graphics[0].end, (0.8, 0.8))
        # select it
        self.image_panel.graphic_selection.set(0)
        self.simulate_drag((200,200), (300,400))
        self.assertClosePoint(self.data_item.graphics[0].start, (0.3, 0.4))
        self.assertClosePoint(self.data_item.graphics[0].end, (0.8, 0.8))
        # shift drag a part, should not deselect and should align horizontally
        self.simulate_drag((300,400), (350,400), Test.KeyboardModifiers(shift=True))
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertClosePoint(self.data_item.graphics[0].start, (0.35, 0.8))

    # this test is DISABLED until the image panel zoom works again
    def test_map_widget_to_image(self):
        # assumes the test widget is 640x480
        self.image_panel.image_canvas_item.update_layout((0, 0), (640, 480))
        self.assertIsNotNone(self.image_panel.map_widget_to_image((240, 320)))
        self.assertClosePoint(self.image_panel.map_widget_to_image((240, 320)), (500.0, 500.0))
        self.assertClosePoint(self.image_panel.map_widget_to_image((0, 80)), (0.0, 0.0))
        self.assertClosePoint(self.image_panel.map_widget_to_image((480, 560)), (1000.0, 1000.0))

    def test_resize_rectangle(self):
        # add rect (0.25, 0.25), (0.5, 0.5)
        self.document_controller.add_rectangle_graphic()
        # make sure items it is in the right place
        self.assertClosePoint(self.data_item.graphics[0].bounds[0], (0.25, 0.25))
        self.assertClosePoint(self.data_item.graphics[0].bounds[1], (0.5, 0.5))
        # select it
        self.image_panel.graphic_selection.set(0)
        # drag top left corner
        self.simulate_drag((250,250), (300,250))
        self.assertClosePoint(self.data_item.graphics[0].bounds[0], (0.30, 0.25))
        self.assertClosePoint(self.data_item.graphics[0].bounds[1], (0.45, 0.5))
        # drag with shift key
        self.simulate_drag((300,250), (350,250), Test.KeyboardModifiers(shift=True))
        self.assertClosePoint(self.data_item.graphics[0].bounds[0], (0.35, 0.35))
        self.assertClosePoint(self.data_item.graphics[0].bounds[1], (0.4, 0.4))

    def test_resize_nonsquare_rectangle(self):
        self.image_panel.image_canvas_item.update_layout((0, 0), (1000, 2000))
        self.data_item = self.document_controller.document_model.set_data_by_key("test", numpy.zeros((2000, 1000)))
        # add rect (0.25, 0.25), (0.5, 0.5)
        self.document_controller.add_rectangle_graphic()
        # make sure items it is in the right place
        self.assertClosePoint(self.data_item.graphics[0].bounds[0], (0.25, 0.25))
        self.assertClosePoint(self.data_item.graphics[0].bounds[1], (0.5, 0.5))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[0]), (500, 250))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[1]), (1000, 500))
        # select it
        self.image_panel.graphic_selection.set(0)
        # drag top left corner
        self.simulate_drag((500,250), (800,250))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[0]), (800, 250))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[1]), (700, 500))
        # drag with shift key
        self.simulate_drag((800,250), (900,250), Test.KeyboardModifiers(shift=True))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[0]), (1000, 250))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[1]), (500, 500))

    def test_resize_nonsquare_ellipse(self):
        self.image_panel.image_canvas_item.update_layout((0, 0), (1000, 2000))
        self.data_item = self.document_controller.document_model.set_data_by_key("test", numpy.zeros((2000, 1000)))
        # add rect (0.25, 0.25), (0.5, 0.5)
        self.document_controller.add_ellipse_graphic()
        # make sure items it is in the right place
        self.assertClosePoint(self.data_item.graphics[0].bounds[0], (0.25, 0.25))
        self.assertClosePoint(self.data_item.graphics[0].bounds[1], (0.5, 0.5))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[0]), (500, 250))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[1]), (1000, 500))
        # select it
        self.image_panel.graphic_selection.set(0)
        # drag top left corner
        self.simulate_drag((500,250), (800,250), Test.KeyboardModifiers(alt=True))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[0]), (800, 250))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[1]), (400, 500))
        # drag with shift key
        self.simulate_drag((800,250), (900,250), Test.KeyboardModifiers(shift=True, alt=True))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[0]), (900, 400))
        self.assertClosePoint(self.image_panel.map_image_norm_to_image(self.data_item.graphics[0].bounds[1]), (200, 200))

    def test_insert_remove_graphics_and_selection(self):
        self.assertFalse(self.image_panel.graphic_selection.indexes)
        self.document_controller.add_rectangle_graphic()
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertTrue(0 in self.image_panel.graphic_selection.indexes)
        graphic = Graphics.RectangleGraphic()
        graphic.bounds = ((0.5,0.5), (0.25,0.25))
        self.data_item.graphics.insert(0, graphic)
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertTrue(1 in self.image_panel.graphic_selection.indexes)
        del self.data_item.graphics[0]
        self.assertEqual(len(self.image_panel.graphic_selection.indexes), 1)
        self.assertTrue(0 in self.image_panel.graphic_selection.indexes)

if __name__ == '__main__':
    unittest.main()

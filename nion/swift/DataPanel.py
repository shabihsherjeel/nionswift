# standard libraries
import asyncio
import copy
import functools
import gettext
import logging
import pkgutil
import threading
import typing
import uuid

# third party libraries
# None

# local libraries
from nion.swift import MimeTypes
from nion.swift import Panel
from nion.swift import Thumbnails
from nion.swift.model import DataGroup
from nion.swift.model import DataItem
from nion.swift.model import DisplayItem
from nion.ui import CanvasItem
from nion.ui import DrawingContext
from nion.ui import GridCanvasItem
from nion.ui import ListCanvasItem
from nion.ui import Widgets
from nion.utils import Event
from nion.utils import Geometry
from nion.utils import ListModel

_ = gettext.gettext


"""
    The data panel has two parts:

    (1) a selection of what collection is displayed, which may be a data group, smart data group,
    or the whole document. If the whole document, then an optional filter may also be applied.
    "within the last 24 hours" would be an example filter.

    (2) the list of display items from the collection. the user may further refine the list of
    items by filtering by additional criteria. the user also chooses the sorting on the list of
    display items.

"""


# support the 'count' display for data groups. count the display items that are children of the container (which
# can be a data group or a document controller) and also display items in all of their child groups.
def get_display_item_count_flat(document_model, container) -> int:

    def append_display_item_flat(container, display_items):
        if isinstance(container, DataItem.DataItem):
            display_items.append(document_model.get_display_item_for_data_item(container))
        if hasattr(container, "data_items"):
            for data_item in container.data_items:
                append_display_item_flat(data_item, display_items)

    display_items = []
    append_display_item_flat(container, display_items)
    return len(display_items)


class DisplayItemAdapter:
    """ Provide a simplified interface to a display item for the purpose of display.

        The display_item property is always valid.

        There is typically one display item associated with each display item.

        Provides the following interface:
            (method) close()
            (property, read-only) display_item
            (property, read-only) title_str
            (property, read-only) datetime_str
            (property, read-only) format_str
            (property, read-only) status_str
            (method) drag_started(ui, x, y, modifiers), returns mime_data, thumbnail_data
            (event) needs_update_event
    """

    def __init__(self, display_item: DisplayItem.DisplayItem, ui):
        self.__display_item = display_item
        self.ui = ui
        self.needs_update_event = Event.Event()

        def display_item_changed():
            self.needs_update_event.fire()

        self.__display_changed_event_listener = display_item.item_changed_event.listen(display_item_changed) if display_item else None

        self.__thumbnail_updated_event_listener = None
        self.__thumbnail_source = None

    def close(self):
        # remove the listener.
        if self.__thumbnail_updated_event_listener:
            self.__thumbnail_updated_event_listener.close()
            self.__thumbnail_updated_event_listener = None
        if self.__thumbnail_source:
            self.__thumbnail_source.close()
            self.__thumbnail_source = None
        if self.__display_changed_event_listener:
            self.__display_changed_event_listener.close()
            self.__display_changed_event_listener = None

    @property
    def display_item(self) -> DisplayItem.DisplayItem:
        return self.__display_item

    @property
    def data_item(self) -> DataItem.DataItem:
        return self.__display_item.data_item if self.__display_item else None

    def __create_thumbnail_source(self):
        # grab the display specifier and if there is a display, handle thumbnail updating.
        if self.__display_item and not self.__thumbnail_source:
            self.__thumbnail_source = Thumbnails.ThumbnailManager().thumbnail_source_for_display_item(self.ui, self.__display_item)

            def thumbnail_updated():
                self.needs_update_event.fire()

            self.__thumbnail_updated_event_listener = self.__thumbnail_source.thumbnail_updated_event.listen(thumbnail_updated)

    def __create_thumbnail(self, draw_rect):
        drawing_context = DrawingContext.DrawingContext()
        if self.__display_item:
            self.__create_thumbnail_source()
            thumbnail_data = self.__thumbnail_source.thumbnail_data
            if thumbnail_data is not None:
                draw_rect = Geometry.fit_to_size(draw_rect, thumbnail_data.shape)
                drawing_context.draw_image(thumbnail_data, draw_rect[0][1], draw_rect[0][0], draw_rect[1][1], draw_rect[1][0])
        return drawing_context

    @property
    def title_str(self) -> str:
        return self.__display_item.displayed_title if self.__display_item else str()

    @property
    def format_str(self) -> str:
        return self.__display_item.size_and_data_format_as_string if self.__display_item else str()

    @property
    def datetime_str(self) -> str:
        return self.__display_item.date_for_sorting_local_as_string if self.__display_item else str()

    @property
    def status_str(self) -> str:
        return self.__display_item.status_str if self.__display_item else str()

    def drag_started(self, ui, x, y, modifiers):
        if self.__display_item:
            mime_data = self.ui.create_mime_data()
            if self.__display_item:
                mime_data.set_data_as_string(MimeTypes.DISPLAY_ITEM_MIME_TYPE, str(self.__display_item.uuid))
            self.__create_thumbnail_source()
            thumbnail_data = self.__thumbnail_source.thumbnail_data if self.__thumbnail_source else None
            return mime_data, thumbnail_data
        return None, None

    def draw_list_item(self, drawing_context, rect):
        with drawing_context.saver():
            draw_rect = ((rect[0][0] + 4, rect[0][1] + 4), (72, 72))
            drawing_context.add(self.__create_thumbnail(draw_rect))
            drawing_context.fill_style = "#000"
            drawing_context.font = "11px serif"
            drawing_context.fill_text(self.title_str, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12)
            drawing_context.fill_text(self.format_str, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12 + 15)
            drawing_context.fill_text(self.datetime_str, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12 + 15 + 15)
            drawing_context.fill_text(self.status_str, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12 + 15 + 15 + 15)

    def draw_grid_item(self, drawing_context, rect):
        drawing_context.add(self.__create_thumbnail(rect.inset(6)))


class DataListController:
    """Control a list of display items in a list widget.

    The following properties are available:
        selected_indexes (r/o)

    The following methods can be called:
        close()

    The controller provides the following callbacks:
        on_delete_display_item_adapters(display_item_adapters)
        on_key_pressed(key)
        on_display_item_adapter_selection_changed(display_item_adapters)
        on_focus_changed(focused)
        on_context_menu_event(display_item_adapter, x, y, gx, gy)

    Display items should respond to these properties and methods and events:
        (method) close()
        (property, read-only) title_str
        (property, read-only) datetime_str
        (property, read-only) format_str
        (property, read-only) status_str
        (method) draw_list_item(drawing_context, draw_rect)
        (method) drag_started(ui, x, y, modifiers), returns mime_data, thumbnail_data
    """

    def __init__(self, event_loop: asyncio.AbstractEventLoop, ui, display_item_adapters_model, selection):
        super().__init__()
        self.__event_loop = event_loop
        self.__pending_tasks = list()
        self.ui = ui
        self.__selection = selection
        self.on_delete_display_item_adapters = None
        self.on_key_pressed = None

        self.__display_item_adapters = list()
        self.__display_item_adapter_needs_update_listeners = list()

        self.__display_item_adapters_model = display_item_adapters_model
        self.__display_item_adapter_inserted_event_listener = self.__display_item_adapters_model.item_inserted_event.listen(self.__display_item_adapter_inserted)
        self.__display_item_adapter_removed_event_listener = self.__display_item_adapters_model.item_removed_event.listen(self.__display_item_adapter_removed)

        class ListCanvasItemDelegate:
            def __init__(self, data_list_controller):
                self.__data_list_controller = data_list_controller

            @property
            def item_count(self):
                return self.__data_list_controller.display_item_adapter_count

            @property
            def items(self):
                return self.__data_list_controller.display_item_adapters

            def paint_item(self, drawing_context, display_item_adapter, rect, is_selected):
                display_item_adapter.draw_list_item(drawing_context, rect)

            def context_menu_event(self, index, x, y, gx, gy):
                return self.__data_list_controller.context_menu_event(index, x, y, gx, gy)

            def delete_pressed(self):
                self.__data_list_controller._delete_pressed()

            def key_pressed(self, key):
                return self.__data_list_controller._key_pressed(key)

            def drag_started(self, index, x, y, modifiers):
                self.__data_list_controller.drag_started(index, x, y, modifiers)

        self.__list_canvas_item = ListCanvasItem.ListCanvasItem(ListCanvasItemDelegate(self), self.__selection)
        def focus_changed(focused):
            self.__list_canvas_item.update()
            if self.on_focus_changed:
                self.on_focus_changed(focused)
        self.__list_canvas_item.on_focus_changed = focus_changed
        self.scroll_area_canvas_item = CanvasItem.ScrollAreaCanvasItem(self.__list_canvas_item)
        self.scroll_area_canvas_item.auto_resize_contents = True
        self.scroll_bar_canvas_item = CanvasItem.ScrollBarCanvasItem(self.scroll_area_canvas_item)
        self.scroll_group_canvas_item = CanvasItem.CanvasItemComposition()
        self.scroll_group_canvas_item.layout = CanvasItem.CanvasItemRowLayout()
        self.scroll_group_canvas_item.add_canvas_item(self.scroll_area_canvas_item)
        self.scroll_group_canvas_item.add_canvas_item(self.scroll_bar_canvas_item)
        self.canvas_item = self.scroll_group_canvas_item
        def selection_changed():
            self.selected_indexes = list(self.__selection.indexes)
            if callable(self.on_display_item_adapter_selection_changed):
                self.on_display_item_adapter_selection_changed([self.__display_item_adapters[index] for index in list(self.__selection.indexes)])
            self.__list_canvas_item.make_selection_visible()
        self.__selection_changed_listener = self.__selection.changed_event.listen(selection_changed)
        self.selected_indexes = list()
        self.on_display_item_adapter_selection_changed = None
        self.on_context_menu_event = None
        self.on_focus_changed = None
        self.on_drag_started = None

        # changed display items keep track of items whose content has changed
        # the content changed messages may come from a thread so have to be
        # moved to the main thread via this object.
        self.__changed_display_item_adapters = False
        self.__changed_display_item_adapters_mutex = threading.RLock()

        for index, display_item_adapter in enumerate(self.__display_item_adapters_model.display_item_adapters):
            self.__display_item_adapter_inserted("display_item_adapters", display_item_adapter, index)

    def close(self):
        for pending_task in self.__pending_tasks:
            pending_task.cancel()
        self.__pending_tasks = None
        self.__selection_changed_listener.close()
        self.__selection_changed_listener = None
        for display_item_adapter_needs_update_listener in self.__display_item_adapter_needs_update_listeners:
            display_item_adapter_needs_update_listener.close()
        self.__display_item_adapter_needs_update_listeners = None
        self.__display_item_adapter_inserted_event_listener.close()
        self.__display_item_adapter_inserted_event_listener = None
        self.__display_item_adapter_removed_event_listener.close()
        self.__display_item_adapter_removed_event_listener = None
        self.__display_item_adapters = None
        self.on_display_item_adapter_selection_changed = None
        self.on_context_menu_event = None
        self.on_drag_started = None
        self.on_focus_changed = None
        self.on_delete_display_item_adapters = None
        self.on_key_pressed = None

    async def __update_display_item_adapters(self):
        # handle the 'changed' stuff. a call to this function is scheduled
        # whenever __changed_display_item_adapters changes.
        with self.__changed_display_item_adapters_mutex:
            changed_display_item_adapters = self.__changed_display_item_adapters
            self.__changed_display_item_adapters = False
        if changed_display_item_adapters:
            self.__list_canvas_item.update()
        self.__pending_tasks.pop(0)

    def make_selection_visible(self):
        self.__list_canvas_item.make_selection_visible()

    # this message comes from the canvas item when delete key is pressed
    def _delete_pressed(self):
        if callable(self.on_delete_display_item_adapters):
            self.on_delete_display_item_adapters([self.__display_item_adapters[index] for index in self.__selection.indexes])

    # this message comes from the canvas item when a key is pressed
    def _key_pressed(self, key):
        if callable(self.on_key_pressed):
            return self.on_key_pressed(key)
        return False

    @property
    def display_item_adapter_count(self):
        return len(self.__display_item_adapters)

    @property
    def display_item_adapters(self):
        return copy.copy(self.__display_item_adapters)

    def _test_get_display_item_adapter(self, index):
        return self.__display_item_adapters[index]

    def context_menu_event(self, index, x, y, gx, gy):
        if self.on_context_menu_event:
            display_item_adapter = self.__display_item_adapters[index] if index is not None else None
            return self.on_context_menu_event(display_item_adapter, x, y, gx, gy)
        return False

    def drag_started(self, index, x, y, modifiers):
        mime_data, thumbnail_data = self.__display_item_adapters[index].drag_started(self.ui, x, y, modifiers)
        if mime_data:
            if self.on_drag_started:
                self.on_drag_started(mime_data, thumbnail_data)

    def __display_item_adapter_needs_update(self):
        with self.__changed_display_item_adapters_mutex:
            self.__changed_display_item_adapters = True
            self.__pending_tasks.append(self.__event_loop.create_task(self.__update_display_item_adapters()))

    # call this method to insert a display item
    # not thread safe
    def __display_item_adapter_inserted(self, key, display_item_adapter, before_index):
        if key == "display_item_adapters":
            self.__display_item_adapters.insert(before_index, display_item_adapter)
            self.__display_item_adapter_needs_update_listeners.insert(before_index, display_item_adapter.needs_update_event.listen(self.__display_item_adapter_needs_update))
            # tell the icon view to update.
            self.__list_canvas_item.refresh_layout()
            self.__list_canvas_item.update()

    # call this method to remove a display item (by index)
    # not thread safe
    def __display_item_adapter_removed(self, key, display_item_adapter, index):
        if key == "display_item_adapters":
            self.__display_item_adapter_needs_update_listeners[index].close()
            del self.__display_item_adapter_needs_update_listeners[index]
            del self.__display_item_adapters[index]
            self.__list_canvas_item.refresh_layout()
            self.__list_canvas_item.update()


class DataGridController:
    """Control a grid of display items in a grid widget.

    The following properties are available:
        selected_indexes (r/o)

    The following methods can be called:
        close()

    The controller provides the following callbacks:
        on_delete_display_item_adapters(display_item_adapters)
        on_key_pressed(key)
        on_display_item_adapter_selection_changed(display_item_adapters)
        on_display_item_adapter_double_clicked(display_item_adapter)
        on_focus_changed(focused)
        on_context_menu_event(display_item_adapter, x, y, gx, gy)
        on_drag_started(mime_data, thumbnail_data)

    Display items should respond to these properties and methods and events:
        (method) close()
        (property, read-only) thumbnail
        (property, read-only) title_str
        (property, read-only) datetime_str
        (property, read-only) format_str
        (property, read-only) status_str
        (method) draw_grid_item(drawing_context, draw_rect)
        (method) drag_started(ui, x, y, modifiers), returns mime_data, thumbnail_data
    """

    def __init__(self, event_loop: asyncio.AbstractEventLoop, ui, display_item_adapters_model, selection, direction=GridCanvasItem.Direction.Row, wrap=True):
        super().__init__()
        self.__event_loop = event_loop
        self.__pending_tasks = list()
        self.ui = ui
        self.__selection = selection
        self.on_delete_display_item_adapters = None
        self.on_key_pressed = None
        self.on_display_item_adapter_double_clicked = None
        self.on_display_item_adapter_selection_changed = None
        self.on_context_menu_event = None
        self.on_focus_changed = None
        self.on_drag_started = None

        self.__display_item_adapters = list()
        self.__display_item_adapter_needs_update_listeners = list()

        self.__display_item_adapters_model = display_item_adapters_model
        self.__display_item_adapter_inserted_event_listener = self.__display_item_adapters_model.item_inserted_event.listen(self.__display_item_adapter_inserted)
        self.__display_item_adapter_removed_event_listener = self.__display_item_adapters_model.item_removed_event.listen(self.__display_item_adapter_removed)

        class GridCanvasItemDelegate:
            def __init__(self, data_grid_controller):
                self.__data_grid_controller = data_grid_controller

            @property
            def item_count(self):
                return self.__data_grid_controller.display_item_adapter_count

            @property
            def items(self):
                return self.__data_grid_controller.display_item_adapters

            def paint_item(self, drawing_context, display_item_adapter, rect, is_selected):
                display_item_adapter.draw_grid_item(drawing_context, rect)

            def on_context_menu_event(self, index, x, y, gx, gy):
                return self.__data_grid_controller.context_menu_event(index, x, y, gx, gy)

            def on_delete_pressed(self):
                self.__data_grid_controller._delete_pressed()

            def on_key_pressed(self, key):
                return self.__data_grid_controller._key_pressed(key)

            def on_mouse_double_clicked(self, mouse_index, x, y, modifiers):
                return self.__data_grid_controller._double_clicked()

            def on_drag_started(self, index, x, y, modifiers):
                self.__data_grid_controller.drag_started(index, x, y, modifiers)

        self.icon_view_canvas_item = GridCanvasItem.GridCanvasItem(GridCanvasItemDelegate(self), self.__selection, direction, wrap)
        def icon_view_canvas_item_focus_changed(focused):
            self.icon_view_canvas_item.update()
            if self.on_focus_changed:
                self.on_focus_changed(focused)
        self.icon_view_canvas_item.on_focus_changed = icon_view_canvas_item_focus_changed
        self.scroll_area_canvas_item = CanvasItem.ScrollAreaCanvasItem(self.icon_view_canvas_item)
        self.scroll_area_canvas_item.auto_resize_contents = True
        self.scroll_group_canvas_item = CanvasItem.CanvasItemComposition()
        if (wrap and direction == GridCanvasItem.Direction.Row) or (not wrap and direction == GridCanvasItem.Direction.Column):
            self.scroll_bar_canvas_item = CanvasItem.ScrollBarCanvasItem(self.scroll_area_canvas_item)
            self.scroll_group_canvas_item.layout = CanvasItem.CanvasItemRowLayout()
        else:
            self.scroll_bar_canvas_item = CanvasItem.ScrollBarCanvasItem(self.scroll_area_canvas_item, CanvasItem.Orientation.Horizontal)
            self.scroll_group_canvas_item.layout = CanvasItem.CanvasItemColumnLayout()
        self.scroll_group_canvas_item.add_canvas_item(self.scroll_area_canvas_item)
        self.scroll_group_canvas_item.add_canvas_item(self.scroll_bar_canvas_item)
        if False:  # dual scroll bars, leave here for easy testing
            self.vertical_scroll_bar_canvas_item = CanvasItem.ScrollBarCanvasItem(self.scroll_area_canvas_item)
            self.horizontal_scroll_bar_canvas_item = CanvasItem.ScrollBarCanvasItem(self.scroll_area_canvas_item, CanvasItem.Orientation.Horizontal)
            self.scroll_group_canvas_item.layout = CanvasItem.CanvasItemGridLayout(Geometry.IntSize(width=2, height=2))
            self.scroll_group_canvas_item.add_canvas_item(self.scroll_area_canvas_item, Geometry.IntPoint(x=0, y=0))
            self.scroll_group_canvas_item.add_canvas_item(self.vertical_scroll_bar_canvas_item, Geometry.IntPoint(x=1, y=0))
            self.scroll_group_canvas_item.add_canvas_item(self.horizontal_scroll_bar_canvas_item, Geometry.IntPoint(x=0, y=1))

        self.canvas_item = self.scroll_group_canvas_item

        def selection_changed():
            self.selected_indexes = list(self.__selection.indexes)
            if callable(self.on_display_item_adapter_selection_changed):
                self.on_display_item_adapter_selection_changed([self.__display_item_adapters[index] for index in list(self.__selection.indexes)])
            self.icon_view_canvas_item.make_selection_visible()

        self.__selection_changed_listener = self.__selection.changed_event.listen(selection_changed)
        self.selected_indexes = list()

        # changed display items keep track of items whose content has changed
        # the content changed messages may come from a thread so have to be
        # moved to the main thread via this object.
        self.__changed_display_item_adapters = False
        self.__changed_display_item_adapters_mutex = threading.RLock()
        self.__closed = False

        for index, display_item_adapter in enumerate(self.__display_item_adapters_model.display_item_adapters):
            self.__display_item_adapter_inserted("display_item_adapters", display_item_adapter, index)

    def close(self):
        assert not self.__closed
        for pending_task in self.__pending_tasks:
            pending_task.cancel()
        self.__pending_tasks = None
        self.icon_view_canvas_item.detach_delegate()
        self.__selection_changed_listener.close()
        self.__selection_changed_listener = None
        self.__display_item_adapter_inserted_event_listener.close()
        self.__display_item_adapter_inserted_event_listener = None
        self.__display_item_adapter_removed_event_listener.close()
        self.__display_item_adapter_removed_event_listener = None
        for display_item_adapter_needs_update_listener in self.__display_item_adapter_needs_update_listeners:
            display_item_adapter_needs_update_listener.close()
        self.__display_item_adapter_needs_update_listeners = None
        self.__display_item_adapters = None
        self.on_display_item_adapter_selection_changed = None
        self.on_context_menu_event = None
        self.on_drag_started = None
        self.on_focus_changed = None
        self.on_delete_display_item_adapters = None
        self.on_key_pressed = None
        self.on_display_item_adapter_double_clicked = None
        self.__closed = True

    async def __update_display_item_adapters(self):
        with self.__changed_display_item_adapters_mutex:
            changed_display_item_adapters = self.__changed_display_item_adapters
            self.__changed_display_item_adapters = False
        if changed_display_item_adapters:
            self.icon_view_canvas_item.update()
        self.__pending_tasks.pop(0)

    def clear_selection(self):
        self.__selection.clear()

    def make_selection_visible(self):
        self.icon_view_canvas_item.make_selection_visible()

    # this message comes from the canvas item when delete key is pressed
    def _delete_pressed(self):
        if callable(self.on_delete_display_item_adapters):
            self.on_delete_display_item_adapters([self.__display_item_adapters[index] for index in self.__selection.indexes])

    # this message comes from the canvas item when a key is pressed
    def _key_pressed(self, key):
        if callable(self.on_key_pressed):
            return self.on_key_pressed(key)
        return False

    # this message comes from the canvas item when a key is pressed
    def _double_clicked(self):
        if len(self.__selection.indexes) == 1:
            if callable(self.on_display_item_adapter_double_clicked):
                return self.on_display_item_adapter_double_clicked(self.__display_item_adapters[list(self.__selection.indexes)[0]])
        return False

    @property
    def display_item_adapter_count(self):
        return len(self.__display_item_adapters)

    @property
    def display_item_adapters(self):
        return copy.copy(self.__display_item_adapters)

    def context_menu_event(self, index, x, y, gx, gy):
        if self.on_context_menu_event:
            display_item_adapter = self.__display_item_adapters[index] if index is not None else None
            display_item = display_item_adapter.display_item if display_item_adapter else None
            return self.on_context_menu_event(display_item, x, y, gx, gy)
        return False

    def drag_started(self, index, x, y, modifiers):
        mime_data, thumbnail_data = self.__display_item_adapters[index].drag_started(self.ui, x, y, modifiers)
        if mime_data:
            if self.on_drag_started:
                self.on_drag_started(mime_data, thumbnail_data)

    def __display_item_adapter_needs_update(self):
        with self.__changed_display_item_adapters_mutex:
            self.__changed_display_item_adapters = True
            self.__pending_tasks.append(self.__event_loop.create_task(self.__update_display_item_adapters()))

    # call this method to insert a display item
    # not thread safe
    def __display_item_adapter_inserted(self, key, display_item_adapter, before_index):
        if key == "display_item_adapters":
            self.__display_item_adapters.insert(before_index, display_item_adapter)
            self.__display_item_adapter_needs_update_listeners.insert(before_index, display_item_adapter.needs_update_event.listen(self.__display_item_adapter_needs_update))
            # tell the icon view to update.
            self.icon_view_canvas_item.refresh_layout()
            self.icon_view_canvas_item.update()

    # call this method to remove a display item (by index)
    # not thread safe
    def __display_item_adapter_removed(self, key, display_item_adapter, index):
        if key == "display_item_adapters":
            self.__display_item_adapter_needs_update_listeners[index].close()
            del self.__display_item_adapter_needs_update_listeners[index]
            del self.__display_item_adapters[index]
            self.icon_view_canvas_item.refresh_layout()
            self.icon_view_canvas_item.update()


class DataListWidget(Widgets.CompositeWidgetBase):

    def __init__(self, ui, data_list_controller):
        super().__init__(ui.create_column_widget())
        self.data_list_controller = data_list_controller
        data_list_widget = ui.create_canvas_widget()
        data_list_widget.canvas_item.add_canvas_item(data_list_controller.canvas_item)
        self.content_widget.add(data_list_widget)

        def data_list_drag_started(mime_data, thumbnail_data):
            self.drag(mime_data, thumbnail_data)

        data_list_controller.on_drag_started = data_list_drag_started

    def close(self):
        self.data_list_controller.on_drag_started = None
        self.data_list_controller = None
        super().close()


class DataGridWidget(Widgets.CompositeWidgetBase):

    def __init__(self, ui, data_grid_controller):
        super().__init__(ui.create_column_widget())
        self.data_grid_controller = data_grid_controller
        data_grid_widget = ui.create_canvas_widget()
        data_grid_widget.canvas_item.add_canvas_item(data_grid_controller.canvas_item)
        self.content_widget.add(data_grid_widget)

        def data_list_drag_started(mime_data, thumbnail_data):
            self.drag(mime_data, thumbnail_data)

        data_grid_controller.on_drag_started = data_list_drag_started

    def close(self):
        self.data_grid_controller.on_drag_started = None
        self.data_grid_controller = None
        super().close()


class LibraryModelController:
    """Controller for a list of top level library items."""

    def __init__(self, ui, item_controllers):
        """
        item_controllers is a list of objects that have a title property and a on_title_changed callback that gets
        invoked (on the ui thread) when the title changes externally.
        """
        self.ui = ui
        self.item_model_controller = self.ui.create_item_model_controller(["display"])
        self.item_model_controller.on_can_drop_mime_data = self.can_drop_mime_data
        self.item_model_controller.on_item_drop_mime_data = self.item_drop_mime_data
        self.item_model_controller.supported_drop_actions = self.item_model_controller.DRAG | self.item_model_controller.DROP
        self.item_model_controller.mime_types_for_drop = ["text/uri-list"]
        self.on_receive_files = None
        self.__item_controllers = list()
        self.__item_count = 0
        # build the items
        for item_controller in item_controllers:
            self.__append_item_controller(item_controller)

    def close(self):
        self.__item_controllers = None
        self.item_model_controller.close()
        self.item_model_controller = None
        self.on_receive_files = None

    # not thread safe. must be called on ui thread.
    def __append_item_controller(self, item_controller):
        parent_item = self.item_model_controller.root
        self.item_model_controller.begin_insert(self.__item_count, self.__item_count, parent_item.row, parent_item.id)
        item = self.item_model_controller.create_item()
        parent_item.insert_child(self.__item_count, item)
        self.item_model_controller.end_insert()
        # not thread safe. must be called on ui thread.
        def title_changed(title):
            item.data["display"] = title
            self.item_model_controller.data_changed(item.row, item.parent.row, item.parent.id)
        item_controller.on_title_changed = title_changed
        title_changed(item_controller.title)
        self.__item_controllers.append(item_controller)
        self.__item_count += 1

    def can_drop_mime_data(self, mime_data, action, row, parent_row, parent_id):
        if mime_data.has_file_paths:
            return row < 0  # only accept drops ONTO items, not BETWEEN items
        return False

    def item_drop_mime_data(self, mime_data, action, row, parent_row, parent_id):
        if mime_data.has_file_paths:
            if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                return self.item_model_controller.NONE
            if callable(self.on_receive_files):
                if self.on_receive_files(mime_data.file_paths):
                    return self.item_model_controller.COPY
        return self.item_model_controller.NONE


class DataGroupModelController:
    """ A tree model of the data groups. this class watches for changes to the data groups contained in the document
    controller and responds by updating the item model controller associated with the data group tree view widget. it
    also handles drag and drop and keeps the current selection synchronized with the image panel. """

    def __init__(self, ui, document_controller):
        self.ui = ui
        self.__document_controller = document_controller
        self.__document_model = document_controller.document_model
        self.item_model_controller = self.ui.create_item_model_controller(["display", "edit"])
        self.item_model_controller.on_item_set_data = self.item_set_data
        self.item_model_controller.on_can_drop_mime_data = self.can_drop_mime_data
        self.item_model_controller.on_item_drop_mime_data = self.item_drop_mime_data
        self.item_model_controller.on_item_mime_data = self.item_mime_data
        self.item_model_controller.on_remove_rows = self.remove_rows
        self.item_model_controller.supported_drop_actions = self.item_model_controller.DRAG | self.item_model_controller.DROP
        self.item_model_controller.mime_types_for_drop = ["text/uri-list", MimeTypes.DISPLAY_ITEM_MIME_TYPE, MimeTypes.DATA_GROUP_MIME_TYPE]
        self.__document_model_item_inserted_listener = self.__document_model.item_inserted_event.listen(functools.partial(self.__item_inserted, self.__document_model))
        self.__document_model_item_removed_listener = self.__document_model.item_removed_event.listen(functools.partial(self.__item_removed, self.__document_model))
        self.__mapping = { self.__document_model: self.item_model_controller.root }
        self.on_receive_files = None
        self.on_item_count_changed = None
        # add items that already exist
        self.__data_group_property_changed_listeners = dict()
        self.__data_group_item_inserted_listeners = dict()
        self.__data_group_item_removed_listeners = dict()
        self.__data_group_display_item_inserted_listeners = dict()
        self.__data_group_display_item_removed_listeners = dict()
        data_groups = self.__document_model.data_groups
        for index, data_group in enumerate(data_groups):
            self.__item_inserted(self.__document_model, "data_groups", data_group, index)

    def close(self):
        # cheap way to unlisten to everything
        for object in self.__mapping.keys():
            if isinstance(object, DataGroup.DataGroup):
                self.__data_group_display_item_inserted_listeners[object.uuid].close()
                del self.__data_group_display_item_inserted_listeners[object.uuid]
                self.__data_group_display_item_removed_listeners[object.uuid].close()
                del self.__data_group_display_item_removed_listeners[object.uuid]
                self.__data_group_item_inserted_listeners[object.uuid].close()
                del self.__data_group_item_inserted_listeners[object.uuid]
                self.__data_group_item_removed_listeners[object.uuid].close()
                del self.__data_group_item_removed_listeners[object.uuid]
                self.__data_group_property_changed_listeners[object.uuid].close()
                del self.__data_group_property_changed_listeners[object.uuid]
        self.__document_model_item_inserted_listener.close()
        self.__document_model_item_inserted_listener = None
        self.__document_model_item_removed_listener.close()
        self.__document_model_item_removed_listener = None
        self.item_model_controller.close()
        self.item_model_controller = None
        self.on_receive_files = None
        self.on_item_count_changed = None

    def log(self, parent_id=-1, indent=""):
        parent_id = parent_id if parent_id >= 0 else self.item_model_controller.root.id
        for index, child in enumerate(self.item_model_controller.item_from_id(parent_id).children):
            value = child.data["display"] if "display" in child.data else "---"
            logging.debug(indent + str(index) + ": (" + str(child.id) + ") " + value)
            self.log(child.id, indent + "  ")

    def __get_display_item_count_flat(self, container) -> int:
        return get_display_item_count_flat(self.__document_model, container)

    # this message is received when a display item is inserted into one of the
    # groups we're observing.
    def __item_inserted(self, container, key, object, before_index):
        if key == "data_groups":
            # manage the item model
            parent_item = self.__mapping[container]
            self.item_model_controller.begin_insert(before_index, before_index, parent_item.row, parent_item.id)
            count = self.__get_display_item_count_flat(object)
            properties = {
                "display": str(object) + (" (%i)" % count),
                "edit": object.title,
                "data_group": object
            }
            item = self.item_model_controller.create_item(properties)
            parent_item.insert_child(before_index, item)
            self.__mapping[object] = item
            def property_changed(key):
                if key == "title":
                    self.__update_item_count(object)
            self.__data_group_property_changed_listeners[object.uuid] = object.property_changed_event.listen(property_changed)
            self.__data_group_item_inserted_listeners[object.uuid] = object.item_inserted_event.listen(functools.partial(self.__item_inserted, object))
            self.__data_group_item_removed_listeners[object.uuid] = object.item_removed_event.listen(functools.partial(self.__item_removed, object))
            self.__data_group_display_item_inserted_listeners[object.uuid] = object.display_item_inserted_event.listen(self.__display_item_inserted)
            self.__data_group_display_item_removed_listeners[object.uuid] = object.display_item_removed_event.listen(self.__display_item_removed)
            self.item_model_controller.end_insert()
            # recursively insert items that already exist
            data_groups = object.data_groups
            for index, child_data_group in enumerate(data_groups):
                self.__item_inserted(object, "data_groups", child_data_group, index)
            if callable(self.on_item_count_changed):
                self.on_item_count_changed()

    # this message is received when a display item is removed from one of the
    # groups we're observing.
    def __item_removed(self, container, key, object, index):
        if key == "data_groups":
            assert isinstance(object, DataGroup.DataGroup)
            # get parent and item
            parent_item = self.__mapping[container]
            # manage the item model
            self.item_model_controller.begin_remove(index, index, parent_item.row, parent_item.id)
            self.__data_group_display_item_inserted_listeners[object.uuid].close()
            del self.__data_group_display_item_inserted_listeners[object.uuid]
            self.__data_group_display_item_removed_listeners[object.uuid].close()
            del self.__data_group_display_item_removed_listeners[object.uuid]
            self.__data_group_item_inserted_listeners[object.uuid].close()
            del self.__data_group_item_inserted_listeners[object.uuid]
            self.__data_group_item_removed_listeners[object.uuid].close()
            del self.__data_group_item_removed_listeners[object.uuid]
            self.__data_group_property_changed_listeners[object.uuid].close()
            del self.__data_group_property_changed_listeners[object.uuid]
            parent_item.remove_child(parent_item.children[index])
            self.__mapping.pop(object)
            self.item_model_controller.end_remove()
            if callable(self.on_item_count_changed):
                self.on_item_count_changed()

    def __update_item_count(self, data_group):
        assert isinstance(data_group, DataGroup.DataGroup)
        count = self.__get_display_item_count_flat(data_group)
        item = self.__mapping[data_group]
        item.data["display"] = str(data_group) + (" (%i)" % count)
        item.data["edit"] = data_group.title
        self.item_model_controller.data_changed(item.row, item.parent.row, item.parent.id)

    # this method if called when one of our listened to data groups changes
    def __display_item_inserted(self, container, display_item, before_index, moving):
        self.__update_item_count(container)

    # this method if called when one of our listened to data groups changes
    def __display_item_removed(self, container, display_item, index, moving):
        self.__update_item_count(container)

    def item_set_data(self, data, index, parent_row, parent_id):
        data_group = self.item_model_controller.item_value("data_group", index, parent_id)
        if data_group:
            command = self.__document_controller.create_rename_data_group_command(data_group, title=data)
            command.perform()
            self.__document_controller.push_undo_command(command)
            return True
        return False

    def get_data_group(self, index, parent_row, parent_id):
        return self.item_model_controller.item_value("data_group", index, parent_id)

    def get_data_group_of_parent(self, parent_row, parent_id):
        parent_item = self.item_model_controller.item_from_id(parent_id)
        return parent_item.data["data_group"] if "data_group" in parent_item.data else None

    def get_data_group_index(self, data_group):
        item = None
        data_group_item = self.__mapping.get(data_group)
        parent_item = data_group_item.parent if data_group_item else self.item_model_controller.root
        assert parent_item is not None
        for child in parent_item.children:
            child_data_group = child.data.get("data_group")
            if child_data_group == data_group:
                item = child
                break
        if item:
            return item.row, item.parent.row, item.parent.id
        else:
            return -1, -1, 0

    def can_drop_mime_data(self, mime_data, action, row, parent_row, parent_id):
        data_group = self.get_data_group_of_parent(parent_row, parent_id)
        if data_group and mime_data.has_file_paths:
            return row < 0  # only accept drops ONTO items, not BETWEEN items
        if data_group and (mime_data.has_format(MimeTypes.DISPLAY_ITEM_MIME_TYPE)):
            if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                return False
            # if the display item exists in this document, then it is copied to the
            # target group. if it doesn't exist in this document, then it is coming
            # from another document and can't be handled here.
            display_item_uuid = uuid.UUID(mime_data.data_as_string(MimeTypes.DISPLAY_ITEM_MIME_TYPE))
            display_item = self.__document_model.get_display_item_by_uuid(display_item_uuid)
            if display_item:
                return True
            return False
        if mime_data.has_format(MimeTypes.DATA_GROUP_MIME_TYPE):
            data_group_uuid = uuid.UUID(mime_data.data_as_string(MimeTypes.DATA_GROUP_MIME_TYPE))
            data_group = self.__document_model.get_data_group_by_uuid(data_group_uuid)
            if data_group:
                return True
        return False

    def item_drop_mime_data(self, mime_data, action, row, parent_row, parent_id):
        data_group = self.get_data_group_of_parent(parent_row, parent_id)
        container = self.__document_model if parent_row < 0 and parent_id == 0 else data_group
        if data_group and mime_data.has_file_paths:
            if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                return self.item_model_controller.NONE
            if callable(self.on_receive_files):
                if self.on_receive_files(mime_data.file_paths, data_group, len(data_group.display_items)):
                    return self.item_model_controller.COPY
        if data_group and (mime_data.has_format(MimeTypes.DISPLAY_ITEM_MIME_TYPE)):
            if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                return self.item_model_controller.NONE
            # if the display item exists in this document, then it is copied to the
            # target group. if it doesn't exist in this document, then it is coming
            # from another document and can't be handled here.
            display_item_uuid = uuid.UUID(mime_data.data_as_string(MimeTypes.DISPLAY_ITEM_MIME_TYPE))
            display_item = self.__document_model.get_display_item_by_uuid(display_item_uuid)
            if display_item:
                command = self.__document_controller.create_insert_data_group_display_item_command(data_group, len(data_group.display_items), display_item)
                command.perform()
                self.__document_controller.push_undo_command(command)
                return action
            return self.item_model_controller.NONE
        if mime_data.has_format(MimeTypes.DATA_GROUP_MIME_TYPE):
            data_group_uuid = uuid.UUID(mime_data.data_as_string(MimeTypes.DATA_GROUP_MIME_TYPE))
            data_group = self.__document_model.get_data_group_by_uuid(data_group_uuid)
            if data_group:
                data_group_copy = copy.deepcopy(data_group)
                row = row if row >= 0 else len(container.data_groups)
                command = self.__document_controller.create_insert_data_group_command(container, row, data_group_copy)
                command.perform()
                self.__document_controller.push_undo_command(command)
                return action
        return self.item_model_controller.NONE

    def item_mime_data(self, index, parent_row, parent_id):
        data_group = self.get_data_group(index, parent_row, parent_id)
        if data_group:
            mime_data = self.ui.create_mime_data()
            mime_data.set_data_as_string(MimeTypes.DATA_GROUP_MIME_TYPE, str(data_group.uuid))
            return mime_data
        return None

    def remove_rows(self, row, count, parent_row, parent_id):
        data_group = self.get_data_group_of_parent(parent_row, parent_id)
        container = self.__document_model if parent_row < 0 and parent_id == 0 else data_group
        for i in range(count):
            del container.data_groups[row]
        return True


class DisplayItemController:

    def __init__(self, base_title, display_items_model, document_controller):
        self.__base_title = base_title
        self.__count = 0
        self.__display_items_model = display_items_model
        self.on_title_changed = None

        # not thread safe. must be called on ui thread.
        def display_item_inserted(key, display_item, before_index):
            self.__count += 1
            if self.on_title_changed:
                document_controller.queue_task(functools.partial(self.on_title_changed, self.title))

        # not thread safe. must be called on ui thread.
        def display_item_removed(key, display_item, index):
            self.__count -= 1
            if self.on_title_changed:
                document_controller.queue_task(functools.partial(self.on_title_changed, self.title))

        self.__display_item_inserted_listener = self.__display_items_model.item_inserted_event.listen(display_item_inserted)
        self.__display_item_removed_listener = self.__display_items_model.item_removed_event.listen(display_item_removed)

        self.__count = len(self.__display_items_model.display_items)

        self.__active_projects_changed_event_listener = document_controller.active_projects_changed_event.listen(display_items_model.mark_changed)

    def close(self):
        self.__display_item_inserted_listener.close()
        self.__display_item_inserted_listener = None
        self.__display_item_removed_listener.close()
        self.__display_item_removed_listener = None
        self.__display_items_model.close()
        self.__active_projects_changed_event_listener.close()
        self.__active_projects_changed_event_listener = None
        self.on_title_changed = None

    @property
    def title(self):
        return self.__base_title + (" (%i)" % self.__count)

    @property
    def is_smart_collection(self) -> bool:
        return not isinstance(self.__display_items_model.container, DataGroup.DataGroup)

    @property
    def filter_id(self) -> typing.Optional[str]:
        return self.__display_items_model.filter_id if self.is_smart_collection else None

    @property
    def data_group(self) -> typing.Optional[DataGroup.DataGroup]:
        return self.__display_items_model.container if not self.is_smart_collection else None


class DataPanel(Panel.Panel):

    def __init__(self, document_controller, panel_id, properties):
        super().__init__(document_controller, panel_id, _("Data Items"))

        ui = document_controller.ui

        self.__filter_changed_event_listener = document_controller.filter_changed_event.listen(self.__data_panel_filter_changed)

        all_items_controller = DisplayItemController(_("All"), document_controller.create_display_items_model(None, "all"), document_controller)
        persistent_items_controller = DisplayItemController(_("Persistent"), document_controller.create_display_items_model(None, "persistent"), document_controller)
        live_items_controller = DisplayItemController(_("Live"), document_controller.create_display_items_model(None, "temporary"), document_controller)
        latest_items_controller = DisplayItemController(_("Latest Session"), document_controller.create_display_items_model(None, "latest-session"), document_controller)
        self.__item_controllers = [all_items_controller, persistent_items_controller, live_items_controller, latest_items_controller]

        self.library_model_controller = LibraryModelController(ui, self.__item_controllers)
        self.library_model_controller.on_receive_files = self.library_model_receive_files

        self.data_group_model_controller = DataGroupModelController(ui, document_controller)
        self.data_group_model_controller.on_receive_files = lambda file_paths, data_group, index: self.data_group_model_receive_files(file_paths, data_group, index)

        self.__blocked1 = False
        self.__blocked2 = False

        def library_widget_selection_changed(selected_indexes):
            # this message comes from the collection tree widget when the selection changes
            if not self.__blocked1:
                self.__blocked1 = True
                try:
                    index = selected_indexes[0][0] if len(selected_indexes) > 0 else -1
                    if index == 3:
                        document_controller.set_filter("latest-session")
                    elif index == 2:
                        document_controller.set_filter("temporary")
                    elif index == 1:
                        document_controller.set_filter("persistent")
                    else:
                        document_controller.set_filter("all")
                finally:
                    self.__blocked1 = False

        self.library_widget = ui.create_tree_widget()
        self.library_widget.item_model_controller = self.library_model_controller.item_model_controller
        self.library_widget.on_selection_changed = library_widget_selection_changed
        self.library_widget.on_focus_changed = lambda focused: setattr(self, "focused", focused)

        self.library_model_controller.on_item_count_changed = self.library_widget.size_to_content
        self.library_widget.size_to_content()

        def data_group_widget_selection_changed(selected_indexes):
            # this message comes from the data group tree widget when the selection changes
            if not self.__blocked2:
                self.__blocked2 = True
                try:
                    if len(selected_indexes) > 0:
                        index, parent_row, parent_id = selected_indexes[0]
                        data_group = self.data_group_model_controller.get_data_group(index, parent_row, parent_id)
                    else:
                        data_group = None
                    document_controller.set_data_group(data_group)
                finally:
                    self.__blocked2 = False

        def data_group_widget_key_pressed(index, parent_row, parent_id, key):
            if key.is_delete:
                data_group = self.data_group_model_controller.get_data_group(index, parent_row, parent_id)
                if data_group:
                    container = self.data_group_model_controller.get_data_group_of_parent(parent_row, parent_id)
                    container = container if container else self.document_controller.document_model
                    self.document_controller.remove_data_group_from_container(data_group, container)
                return True
            return False

        self.data_group_widget = ui.create_tree_widget()
        self.data_group_widget.item_model_controller = self.data_group_model_controller.item_model_controller
        self.data_group_widget.on_selection_changed = data_group_widget_selection_changed
        self.data_group_widget.on_item_key_pressed = data_group_widget_key_pressed
        self.data_group_widget.on_focus_changed = lambda focused: setattr(self, "focused", focused)

        self.data_group_model_controller.on_item_count_changed = self.data_group_widget.size_to_content
        self.data_group_widget.size_to_content()

        library_label_row = ui.create_row_widget()
        library_label_row.add_spacing(8)
        library_label_row.add(ui.create_label_widget(_("Library"), properties={"stylesheet": "font-weight: bold"}))
        library_label_row.add_stretch()

        collections_label_row = ui.create_row_widget()
        collections_label_row.add_spacing(8)
        collections_label_row.add(ui.create_label_widget(_("Collections"), properties={"stylesheet": "font-weight: bold"}))
        collections_label_row.add_stretch()

        library_section_widget = ui.create_column_widget()
        library_section_widget.add_spacing(4)
        library_section_widget.add(library_label_row)
        library_section_widget.add(self.library_widget)
        library_section_widget.add_spacing(4)
        library_section_widget.add(collections_label_row)
        library_section_widget.add(self.data_group_widget)
        library_section_widget.add_spacing(4)
        library_section_widget.add_stretch()

        def show_context_menu(display_item_adapter, x, y, gx, gy):
            menu = document_controller.create_context_menu_for_display(display_item_adapter.display_item, use_selection=True)
            menu.popup(gx, gy)
            return True

        def map_display_item_to_display_item_adapter(display_item):
            return DisplayItemAdapter(display_item, ui)

        def unmap_display_item_to_display_item_adapter(display_item_adapter):
            display_item_adapter.close()

        self.__filtered_display_item_adapters_model = ListModel.MappedListModel(container=document_controller.filtered_display_items_model, master_items_key="display_items", items_key="display_item_adapters", map_fn=map_display_item_to_display_item_adapter, unmap_fn=unmap_display_item_to_display_item_adapter)

        self.__selection = self.document_controller.selection

        self.__focused = False

        def selection_changed():
            # called when the selection changes; notify selected display item changed if focused.
            self.__notify_focus_changed()

        self.__selection_changed_event_listener = self.__selection.changed_event.listen(selection_changed)

        def display_item_adapter_selection_changed(display_item_adapters):
            indexes = set()
            for index, display_item_adapter in enumerate(self.__filtered_display_item_adapters_model.display_item_adapters):
                if display_item_adapter in display_item_adapters:
                    indexes.add(index)
            self.__selection.set_multiple(indexes)
            self.__notify_focus_changed()

        def focus_changed(focused):
            self.focused = focused

        def delete_display_item_adapters(display_item_adapters):
            document_controller.delete_display_items([display_item_adapter.display_item for display_item_adapter in display_item_adapters])

        self.data_list_controller = DataListController(document_controller.event_loop, ui, self.__filtered_display_item_adapters_model, self.__selection)
        self.data_list_controller.on_display_item_adapter_selection_changed = display_item_adapter_selection_changed
        self.data_list_controller.on_context_menu_event = show_context_menu
        self.data_list_controller.on_focus_changed = focus_changed
        self.data_list_controller.on_delete_display_item_adapters = delete_display_item_adapters

        self.data_grid_controller = DataGridController(document_controller.event_loop, ui, self.__filtered_display_item_adapters_model, self.__selection)
        self.data_grid_controller.on_display_item_adapter_selection_changed = display_item_adapter_selection_changed
        self.data_grid_controller.on_context_menu_event = show_context_menu
        self.data_grid_controller.on_focus_changed = focus_changed
        self.data_grid_controller.on_delete_display_item_adapters = delete_display_item_adapters

        data_list_widget = DataListWidget(ui, self.data_list_controller)
        data_grid_widget = DataGridWidget(ui, self.data_grid_controller)

        list_icon_button = CanvasItem.BitmapButtonCanvasItem(CanvasItem.load_rgba_data_from_bytes(pkgutil.get_data(__name__, "resources/list_icon_20.png")))
        grid_icon_button = CanvasItem.BitmapButtonCanvasItem(CanvasItem.load_rgba_data_from_bytes(pkgutil.get_data(__name__, "resources/grid_icon_20.png")))

        list_icon_button.sizing.set_fixed_size(Geometry.IntSize(20, 20))
        grid_icon_button.sizing.set_fixed_size(Geometry.IntSize(20, 20))

        button_row = CanvasItem.CanvasItemComposition()
        button_row.layout = CanvasItem.CanvasItemRowLayout(spacing=4)
        button_row.add_canvas_item(list_icon_button)
        button_row.add_canvas_item(grid_icon_button)

        buttons_widget = ui.create_canvas_widget(properties={"height": 20, "width": 44})
        buttons_widget.canvas_item.add_canvas_item(button_row)

        search_widget = ui.create_row_widget()
        search_widget.add_spacing(8)
        search_widget.add(ui.create_label_widget(_("Filter")))
        search_widget.add_spacing(8)
        search_line_edit = ui.create_line_edit_widget()
        search_line_edit.placeholder_text = _("No Filter")
        # search_line_edit.clear_button_enabled = True  # Qt 5.3 doesn't signal text edited or editing finished when clearing. useless so disabled.
        search_line_edit.on_text_edited = self.document_controller.filter_controller.text_filter_changed
        search_line_edit.on_editing_finished = self.document_controller.filter_controller.text_filter_changed
        search_widget.add(search_line_edit)
        search_widget.add_spacing(6)
        search_widget.add(buttons_widget)
        search_widget.add_spacing(8)

        self.data_view_widget = ui.create_stack_widget()
        self.data_view_widget.add(data_list_widget)
        self.data_view_widget.add(data_grid_widget)
        self.data_view_widget.current_index = 0

        self.__view_button_group = CanvasItem.RadioButtonGroup([list_icon_button, grid_icon_button])
        self.__view_button_group.current_index = 0
        self.__view_button_group.on_current_index_changed = lambda index: setattr(self.data_view_widget, "current_index", index)

        slave_widget = ui.create_column_widget()
        slave_widget.add(self.data_view_widget)
        slave_widget.add_spacing(6)
        slave_widget.add(search_widget)
        slave_widget.add_spacing(6)

        self.splitter = ui.create_splitter_widget("vertical", properties)
        self.splitter.orientation = "vertical"
        self.splitter.add(library_section_widget)
        self.splitter.add(slave_widget)
        self.splitter.set_sizes([1, 9999])  # minimum library section; maximum display item section
        self.splitter.restore_state("window/v1/data_panel_splitter")

        self.widget = self.splitter

        self._data_list_widget = data_list_widget
        self._data_grid_widget = data_grid_widget

        data_group, filter_id = document_controller.get_data_group_and_filter_id()
        self.__data_panel_filter_changed(data_group, filter_id)

    def close(self):
        # display items model should not be closed since it isn't created in this object
        self.splitter.save_state("window/v1/data_panel_splitter")
        # close the widget to stop repainting the widgets before closing the controllers.
        super().close()
        # finish closing
        self.data_list_controller.close()
        self.data_list_controller = None
        self.data_grid_controller.close()
        self.data_grid_controller = None
        # close the item models
        self.data_group_model_controller.close()
        self.data_group_model_controller = None
        for item_controller in self.__item_controllers:
            item_controller.close()
        self.library_model_controller.close()
        self.library_model_controller = None
        # and the listeners
        self.__filter_changed_event_listener.close()
        self.__filter_changed_event_listener = None
        self.__filtered_display_item_adapters_model.close()
        self.__filtered_display_item_adapters_model = None
        # button group
        self.__view_button_group.close()
        self.__view_button_group = None

    def __notify_focus_changed(self):
        if self.__focused:
            if len(self.__selection.indexes) == 1:
                display_item_adapter = self.__filtered_display_item_adapters_model.display_item_adapters[list(self.__selection.indexes)[0]]
                self.document_controller.notify_focused_display_changed(display_item_adapter.display_item)
            else:
                self.document_controller.notify_focused_display_changed(None)

    @property
    def focused(self):
        return self.__focused

    @focused.setter
    def focused(self, value):
        self.__focused = value
        self.__notify_focus_changed()

    def __data_panel_filter_changed(self, data_group, filter_id):
        if data_group:
            index, parent_row, parent_id = self.data_group_model_controller.get_data_group_index(data_group)
            self.library_widget.clear_current_row()
            self.data_group_widget.set_current_row(index, parent_row, parent_id)
        else:
            self.data_group_widget.clear_current_row()
            if filter_id == "latest-session":
                self.library_widget.set_current_row(3, -1, 0)  # select the 'latest' group
            elif filter_id == "temporary":
                self.library_widget.set_current_row(2, -1, 0)  # select the 'live' group
            elif filter_id == "persistent":
                self.library_widget.set_current_row(1, -1, 0)  # select the 'persistent' group
            else:
                self.library_widget.set_current_row(0, -1, 0)  # select the 'all' group

    def library_model_receive_files(self, file_paths, threaded=True):
        self.document_controller._register_ui_activity()

        def receive_files_complete(received_data_items):
            def select_library_all():
                self.document_controller.select_data_items_in_data_panel([received_data_items[0]])
            if len(received_data_items) > 0:
                self.queue_task(select_library_all)

        self.document_controller.receive_files(file_paths, None, -1, threaded, receive_files_complete)
        return True

    # receive files dropped into the data group.
    # this message comes from the data group model, which is why it is named the way it is.
    def data_group_model_receive_files(self, file_paths, data_group, index, threaded=True):
        def receive_files_complete(received_data_items):
            def select_data_group_and_data_item():
                self.document_controller.set_data_group(data_group)
                self.document_controller.select_data_items_in_data_panel([received_data_items[0]])
            if len(received_data_items) > 0:
                if threaded:
                    self.queue_task(select_data_group_and_data_item)
                else:
                    select_data_group_and_data_item()

        self.document_controller.receive_files(file_paths, data_group, index, threaded, receive_files_complete)
        return True

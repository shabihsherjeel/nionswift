# standard libraries
import copy
import functools
import gettext
import logging
import threading
import uuid
import weakref

# third party libraries
# None

# local libraries
from nion.swift import Panel
from nion.swift.model import DataGroup
from nion.swift.model import DataItem
from nion.ui import Binding
from nion.ui import Geometry
from nion.ui import Process

_ = gettext.gettext


"""
    The data panel has two parts:

    (1) a selection of what collection is displayed, which may be a data group, smart data group,
    or the whole document. If the whole document, then an optional filter may also be applied.
    "within the last 24 hours" would be an example filter.

    (2) the list of data items from the collection. the user may further refine the list of
    items by filtering by additional criteria. the user also chooses the sorting on the list of
    data items.

"""


# persistently store a data specifier
class DataPanelSelection(object):
    def __init__(self, data_group=None, data_item=None, filter_id=None):
        self.__data_group = data_group
        self.__data_item = data_item
        self.__filter_id = filter_id
    def __get_data_group(self):
        return self.__data_group
    data_group = property(__get_data_group)
    def __get_data_item(self):
        return self.__data_item
    data_item = property(__get_data_item)
    def __get_filter_id(self):
        return self.__filter_id
    filter_id = property(__get_filter_id)
    def __str__(self):
        return "(%s,%s)" % (str(self.data_group), str(self.data_item))


class DataPanel(Panel.Panel):

    class LibraryItemController(object):

        def __init__(self, binding):
            self.__task_queue = Process.TaskQueue()
            self.__count = 0
            self.title_updater = None
            self.__binding = binding
            def data_item_inserted(data_item, before_index):
                self.__count += 1
                if self.title_updater:
                    self.title_updater(self.__count)
            def data_item_removed(data_item, index):
                self.__count -= 1
                if self.title_updater:
                    self.title_updater(self.__count)
            self.__binding.inserters[id(self)] = lambda data_item, before_index: self.queue_task(functools.partial(data_item_inserted, data_item, before_index))
            self.__binding.removers[id(self)] = lambda data_item, index: self.queue_task(functools.partial(data_item_removed, data_item, index))
            def update_count():
                self.__count = len(self.__binding.data_items)
                if self.title_updater:
                    self.title_updater(self.__count)
            # make sure the count gets properly initialized
            self.queue_task(update_count)

        def close(self):
            del self.__binding.inserters[id(self)]
            del self.__binding.removers[id(self)]
            self.__binding.close()
            self.__binding = None

        def periodic(self):
            self.__task_queue.perform_tasks()

        # thread safe
        def queue_task(self, task):
            self.__task_queue.put(task)


    class LibraryModelController(object):

        def __init__(self, document_controller):
            self.ui = document_controller.ui
            self.item_model_controller = self.ui.create_item_model_controller(["display"])
            self.item_model_controller.on_item_drop_mime_data = lambda mime_data, action, row, parent_row, parent_id: self.item_drop_mime_data(mime_data, action, row, parent_row, parent_id)
            self.item_model_controller.supported_drop_actions = self.item_model_controller.DRAG | self.item_model_controller.DROP
            self.item_model_controller.mime_types_for_drop = ["text/uri-list", "text/data_item_uuid"]
            self.__document_controller_weakref = weakref.ref(document_controller)
            self.on_receive_files = None
            self.__item_controllers = list()
            self.__item_count = 0
            # build the items
            self.__append_item_controller(_("All"), DataPanel.LibraryItemController(document_controller.create_data_item_binding(None, None)))
            self.__append_item_controller(_("Latest Session"), DataPanel.LibraryItemController(document_controller.create_data_item_binding(None, "latest-session")))

        def close(self):
            for item_controller in self.__item_controllers:
                item_controller.close()
            self.__item_controllers = None
            self.item_model_controller.close()
            self.item_model_controller = None

        def periodic(self):
            for item_controller in self.__item_controllers:
                item_controller.periodic()

        def __get_document_controller(self):
            return self.__document_controller_weakref()
        document_controller = property(__get_document_controller)

        def __append_item_controller(self, title, item_controller):
            parent_item = self.item_model_controller.root
            self.item_model_controller.begin_insert(self.__item_count, self.__item_count, parent_item.row, parent_item.id)
            item = self.item_model_controller.create_item()
            parent_item.insert_child(self.__item_count, item)
            self.item_model_controller.end_insert()
            def title_updater(count):
                item.data["display"] = title + (" (%i)" % count)
                self.item_model_controller.data_changed(item.row, item.parent.row, item.parent.id)
            item_controller.title_updater = title_updater
            title_updater(0)
            self.__item_controllers.append(item_controller)
            self.__item_count += 1

        def item_drop_mime_data(self, mime_data, action, row, parent_row, parent_id):
            container = self.document_controller.document_model
            if mime_data.has_file_paths:
                if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                    return self.item_model_controller.NONE
                if self.on_receive_files and self.on_receive_files(mime_data.file_paths, len(self.document_controller.document_model.data_items)):
                    return self.item_model_controller.COPY
            return self.item_model_controller.NONE


    # a tree model of the data groups. this class watches for changes to the data groups contained in the document controller
    # and responds by updating the item model controller associated with the data group tree view widget. it also handles
    # drag and drop and keeps the current selection synchronized with the image panel.

    class DataGroupModelController(object):

        def __init__(self, document_controller):
            self.ui = document_controller.ui
            self.item_model_controller = self.ui.create_item_model_controller(["display", "edit"])
            self.item_model_controller.on_item_set_data = lambda data, index, parent_row, parent_id: self.item_set_data(data, index, parent_row, parent_id)
            self.item_model_controller.on_item_drop_mime_data = lambda mime_data, action, row, parent_row, parent_id: self.item_drop_mime_data(mime_data, action, row, parent_row, parent_id)
            self.item_model_controller.on_item_mime_data = lambda row, parent_row, parent_id: self.item_mime_data(row, parent_row, parent_id)
            self.item_model_controller.on_remove_rows = lambda row, count, parent_row, parent_id: self.remove_rows(row, count, parent_row, parent_id)
            self.item_model_controller.supported_drop_actions = self.item_model_controller.DRAG | self.item_model_controller.DROP
            self.item_model_controller.mime_types_for_drop = ["text/uri-list", "text/data_item_uuid", "text/data_group_uuid"]
            self.__document_controller_weakref = weakref.ref(document_controller)
            self.document_controller.document_model.add_observer(self)
            self.__mapping = { document_controller.document_model: self.item_model_controller.root }
            self.on_receive_files = None
            # add items that already exist
            data_groups = document_controller.document_model.data_groups
            for index, data_group in enumerate(data_groups):
                self.item_inserted(document_controller.document_model, "data_groups", data_group, index)

        def close(self):
            # cheap way to unlisten to everything
            for object in self.__mapping.keys():
                if isinstance(object, DataGroup.DataGroup):
                    object.remove_listener(self)
                    object.remove_observer(self)
                    object.remove_ref()
            self.document_controller.document_model.remove_observer(self)
            self.item_model_controller.close()
            self.item_model_controller = None

        def log(self, parent_id=-1, indent=""):
            parent_id = parent_id if parent_id >= 0 else self.item_model_controller.root.id
            for index, child in enumerate(self.item_model_controller.item_from_id(parent_id).children):
                value = child.data["display"] if "display" in child.data else "---"
                logging.debug(indent + str(index) + ": (" + str(child.id) + ") " + value)
                self.log(child.id, indent + "  ")

        def __get_document_controller(self):
            return self.__document_controller_weakref()
        document_controller = property(__get_document_controller)

        # these two methods support the 'count' display for data groups. they count up
        # the data items that are children of the container (which can be a data group
        # or a document controller) and also data items in all of their child groups.
        def __append_data_item_flat(self, container, data_items):
            if isinstance(container, DataItem.DataItem):
                data_items.append(container)
            if hasattr(container, "data_items"):
                for child_data_item in container.data_items:
                    self.__append_data_item_flat(child_data_item, data_items)
        def __get_data_item_count_flat(self, container):
            data_items = []
            self.__append_data_item_flat(container, data_items)
            return len(data_items)

        # this message is received when a data item is inserted into one of the
        # groups we're observing.
        def item_inserted(self, container, key, object, before_index):
            if key == "data_groups":
                # manage the item model
                parent_item = self.__mapping[container]
                self.item_model_controller.begin_insert(before_index, before_index, parent_item.row, parent_item.id)
                count = self.__get_data_item_count_flat(object)
                properties = {
                    "display": str(object) + (" (%i)" % count),
                    "edit": object.title,
                    "data_group": object
                }
                item = self.item_model_controller.create_item(properties)
                parent_item.insert_child(before_index, item)
                self.__mapping[object] = item
                object.add_observer(self)
                object.add_listener(self)
                object.add_ref()
                self.item_model_controller.end_insert()
                # recursively insert items that already exist
                data_groups = object.data_groups
                for index, child_data_group in enumerate(data_groups):
                    self.item_inserted(object, "data_groups", child_data_group, index)

        # this message is received when a data item is removed from one of the
        # groups we're observing.
        def item_removed(self, container, key, object, index):
            if key == "data_groups":
                assert isinstance(object, DataGroup.DataGroup)
                # get parent and item
                parent_item = self.__mapping[container]
                # manage the item model
                self.item_model_controller.begin_remove(index, index, parent_item.row, parent_item.id)
                object.remove_listener(self)
                object.remove_observer(self)
                object.remove_ref()
                parent_item.remove_child(parent_item.children[index])
                self.__mapping.pop(object)
                self.item_model_controller.end_remove()

        def __update_item_count(self, data_group):
            assert isinstance(data_group, DataGroup.DataGroup)
            count = self.__get_data_item_count_flat(data_group)
            item = self.__mapping[data_group]
            item.data["display"] = str(data_group) + (" (%i)" % count)
            item.data["edit"] = data_group.title
            self.item_model_controller.data_changed(item.row, item.parent.row, item.parent.id)

        def property_changed(self, data_group, key, value):
            if key == "title":
                self.__update_item_count(data_group)

        # this method if called when one of our listened to data groups changes
        def data_item_inserted(self, container, data_item, before_index, moving):
            self.__update_item_count(container)

        # this method if called when one of our listened to data groups changes
        def data_item_removed(self, container, data_item, index, moving):
            self.__update_item_count(container)

        def item_set_data(self, data, index, parent_row, parent_id):
            data_group = self.item_model_controller.item_value("data_group", index, parent_id)
            if data_group:
                data_group.title = data
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

        def item_drop_mime_data(self, mime_data, action, row, parent_row, parent_id):
            data_group = self.get_data_group_of_parent(parent_row, parent_id)
            container = self.document_controller.document_model if parent_row < 0 and parent_id == 0 else data_group
            if data_group and mime_data.has_file_paths:
                if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                    return self.item_model_controller.NONE
                if self.on_receive_files and self.on_receive_files(mime_data.file_paths, data_group, len(data_group.data_items)):
                    return self.item_model_controller.COPY
            if data_group and mime_data.has_format("text/data_item_uuid"):
                if row >= 0:  # only accept drops ONTO items, not BETWEEN items
                    return self.item_model_controller.NONE
                # if the data item exists in this document, then it is copied to the
                # target group. if it doesn't exist in this document, then it is coming
                # from another document and can't be handled here.
                data_item_uuid = uuid.UUID(mime_data.data_as_string("text/data_item_uuid"))
                data_item = self.document_controller.document_model.get_data_item_by_key(data_item_uuid)
                if data_item:
                    data_item_copy = copy.deepcopy(data_item)
                    self.document_controller.document_model.append_data_item(data_item_copy)
                    data_group.append_data_item(data_item_copy)
                    return action
                return self.item_model_controller.NONE
            if mime_data.has_format("text/data_group_uuid"):
                data_group_uuid = uuid.UUID(mime_data.data_as_string("text/data_group_uuid"))
                data_group = self.document_controller.document_model.get_data_group_by_uuid(data_group_uuid)
                if data_group:
                    data_group_copy = copy.deepcopy(data_group)
                    if row >= 0:
                        container.data_groups.insert(row, data_group_copy)
                    else:
                        container.data_groups.append(data_group_copy)
                    return action
            return self.item_model_controller.NONE

        def item_mime_data(self, index, parent_row, parent_id):
            data_group = self.get_data_group(index, parent_row, parent_id)
            if data_group:
                mime_data = self.ui.create_mime_data()
                mime_data.set_data_as_string("text/data_group_uuid", str(data_group.uuid))
                return mime_data
            return None

        def remove_rows(self, row, count, parent_row, parent_id):
            data_group = self.get_data_group_of_parent(parent_row, parent_id)
            container = self.document_controller.document_model if parent_row < 0 and parent_id == 0 else data_group
            for i in range(count):
                del container.data_groups[row]
            return True


    class DataItemModelController(object):

        """
            There are two levels of bindings:
                __binding binds to the document model or data group and generates a list of data items
                __filter_binding is used by the filter panel to further filter the data items. this is the one that's displayed
        """

        def __init__(self, document_controller):
            self.ui = document_controller.ui
            self.__task_queue = Process.TaskQueue()
            self.__binding = document_controller.filtered_data_items_binding
            self.__data_items = list()  # data items being listened to
            def data_item_inserted(data_item, before_index):
                self.__data_item_inserted(data_item, before_index)
            def data_item_removed(data_item, index):
                self.__data_item_removed(data_item, index)
            self.__binding.inserters[id(self)] = lambda data_item, before_index: self.queue_task(functools.partial(data_item_inserted, data_item, before_index))
            self.__binding.removers[id(self)] = lambda data_item, index: self.queue_task(functools.partial(data_item_removed, data_item, index))
            self.list_model_controller = self.ui.create_list_model_controller(["uuid", "display"])
            self.list_model_controller.on_item_mime_data = lambda row: self.item_mime_data(row)
            self.list_model_controller.supported_drop_actions = self.list_model_controller.DRAG | self.list_model_controller.DROP
            self.__document_controller_weakref = weakref.ref(document_controller)
            # changed data items keep track of items whose content has changed
            # the content changed messages may come from a thread so have to be
            # moved to the main thread via this object.
            self.__changed_data_items = set()
            self.__changed_data_items_mutex = threading.RLock()

        def close(self):
            while len(self.__data_items) > 0:
                self.__data_item_removed(self.__data_items[0], 0)
            del self.__binding.inserters[id(self)]
            del self.__binding.removers[id(self)]
            self.list_model_controller.close()
            self.list_model_controller = None

        def periodic(self):
            self.__task_queue.perform_tasks()
            # handle the 'changed' stuff
            with self.__changed_data_items_mutex:
                changed_data_items = self.__changed_data_items
                self.__changed_data_items = set()
            data_items = copy.copy(self.__binding.data_items)
            # we might be receiving this message for an item that is no longer in the list
            # if the item updates and the user switches panels. check and skip it if so.
            for data_item in changed_data_items:
                if data_item in data_items:
                    index = data_items.index(data_item)
                    properties = self.list_model_controller.model[index]
                    self.list_model_controller.data_changed()

        # thread safe
        def queue_task(self, task):
            self.__task_queue.put(task)

        def __get_document_controller(self):
            return self.__document_controller_weakref()
        document_controller = property(__get_document_controller)

        # container is either a data group or a document model
        def __get_container(self):
            return self.document_controller.data_items_binding.container
        container = property(__get_container)

        def __get_data_items(self):
            return self.__binding.data_items
        data_items = property(__get_data_items)

        def get_data_item_by_index(self, index):
            data_items = self.__binding.data_items
            return data_items[index] if index >= 0 and index < len(data_items) else None

        # return a dict with key value pairs. these methods are here for testing only.
        def _get_model_data(self, index):
            return self.list_model_controller.model[index]
        def _get_model_data_count(self):
            return len(self.list_model_controller.model)

        def remove_data_item(self, data_item):
            container = DataGroup.get_data_item_container(self.container, data_item)
            if container and data_item in container.data_items:
                container.remove_data_item(data_item)

        def get_data_item_index(self, data_item):
            data_items = self.__binding.data_items
            return data_items.index(data_item) if data_item in data_items else -1

        # data_item_content_changed is received from data items tracked in this model.
        # the connection is established in add_data_item using add_listener.
        def data_item_content_changed(self, data_item, changes):
            with self.__changed_data_items_mutex:
                self.__changed_data_items.add(data_item)

        # this method if called when one of our listened to items changes.
        def __data_item_inserted(self, data_item, before_index):
            # add the listener. this will result in calls to data_item_content_changed
            data_item.add_listener(self)
            data_item.add_ref()
            self.__data_items.append(data_item)
            # do the insert
            properties = {
                "uuid": str(data_item.uuid),
                "display": data_item.title,
            }
            self.list_model_controller.begin_insert(before_index, before_index)
            self.list_model_controller.model.insert(before_index, properties)
            self.list_model_controller.end_insert()

        # this method if called when one of our listened to items changes
        def __data_item_removed(self, data_item, index):
            assert isinstance(data_item, DataItem.DataItem)
            # manage the item model
            self.list_model_controller.begin_remove(index, index)
            del self.list_model_controller.model[index]
            self.list_model_controller.end_remove()
            # remove the listener.
            data_item.remove_listener(self)
            data_item.remove_ref()
            self.__data_items.remove(data_item)

        def item_mime_data(self, row):
            data_item = self.get_data_item_by_index(row)
            if data_item:
                mime_data = self.ui.create_mime_data()
                mime_data.set_data_as_string("text/data_item_uuid", str(data_item.uuid))
                return mime_data
            return None

        # this message comes from the styled item delegate
        # data items are actually hierarchical in nature,
        def paint(self, ctx, options):
            rect = ((options["rect"]["top"], options["rect"]["left"]), (options["rect"]["height"], options["rect"]["width"]))
            index = options["index"]["row"]
            data_item = self.get_data_item_by_index(index)
            if not data_item:
                # this can happen when switching views -- data is changed out but model hasn't updated yet (threading).
                # not sure of the best solution here, but I expect that it will present itself over time.
                return
            local_self = self
            def update_thumbail_data(thumbail_data):
                with local_self.__changed_data_items_mutex:
                    local_self.__changed_data_items.add(data_item)
            thumbnail_data = data_item.displays[0].get_processor("thumbnail").get_data(self.ui, completion_fn=update_thumbail_data)
            data = self._get_model_data(index)
            display = data_item.title
            display2 = data_item.size_and_data_format_as_string
            display3 = data_item.datetime_original_as_string
            display4 = data_item.live_status_as_string
            ctx.save()
            if thumbnail_data is not None:
                draw_rect = ((rect[0][0] + 4, rect[0][1] + 4), (72, 72))
                draw_rect = Geometry.fit_to_size(draw_rect, thumbnail_data.shape)
                ctx.draw_image(thumbnail_data, draw_rect[0][1], draw_rect[0][0], draw_rect[1][1], draw_rect[1][0])
            ctx.fill_style = "#000"
            ctx.fill_text(display, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12)
            ctx.font = "11px italic"
            ctx.fill_text(display2, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12 + 15)
            ctx.font = "11px italic"
            ctx.fill_text(display3, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12 + 15 + 15)
            ctx.font = "11px italic"
            ctx.fill_text(display4, rect[0][1] + 4 + 72 + 4, rect[0][0] + 4 + 12 + 15 + 15 + 15)
            ctx.restore()

    def __init__(self, document_controller, panel_id, properties):
        super(DataPanel, self).__init__(document_controller, panel_id, _("Data Items"))

        self.__focused = False
        self.__selection = DataPanelSelection()
        self.__closing = False

        self.__block1 = False

        self.library_model_controller = DataPanel.LibraryModelController(document_controller)
        self.library_model_controller.on_receive_files = lambda file_paths, index: self.library_model_receive_files(file_paths, index)

        self.data_group_model_controller = DataPanel.DataGroupModelController(document_controller)
        self.data_group_model_controller.on_receive_files = lambda file_paths, data_group, index: self.data_group_model_receive_files(file_paths, data_group, index)

        self.data_item_model_controller = DataPanel.DataItemModelController(document_controller)

        def data_item_model_receive_files(file_paths, row, parent_row):
            data_group = self.__selection.data_group
            if parent_row == -1:  # don't accept drops _on top_ of other items
                # row=-1, parent=-1 means dropping outside of any items; so put it at the end
                row = row if row >= 0 else len(data_group.data_items)
                return self.data_group_model_receive_files(file_paths, data_group, row)
            else:
                return False

        self.data_item_model_controller.on_receive_files = data_item_model_receive_files

        ui = document_controller.ui

        def library_widget_selection_changed(selected_indexes):
            if not self.__block1:
                index = selected_indexes[0][0] if len(selected_indexes) > 0 else -1
                if index == 1:
                    self.update_data_panel_selection(DataPanelSelection(filter_id="latest-session"))
                else:
                    self.update_data_panel_selection(DataPanelSelection())

        self.library_widget = ui.create_tree_widget(properties={"height": 24 + 18 * 2})
        self.library_widget.item_model_controller = self.library_model_controller.item_model_controller
        self.library_widget.on_selection_changed = library_widget_selection_changed
        self.library_widget.on_focus_changed = lambda focused: self.__set_focused(focused)

        def data_group_widget_selection_changed(selected_indexes):
            if not self.__block1:
                if len(selected_indexes) > 0:
                    index, parent_row, parent_id = selected_indexes[0]
                    data_group = self.data_group_model_controller.get_data_group(index, parent_row, parent_id)
                else:
                    data_group = None
                self.update_data_panel_selection(DataPanelSelection(data_group, None))

        def data_group_widget_key_pressed(index, parent_row, parent_id, key):
            if key.is_delete:
                data_group = self.data_group_model_controller.get_data_group(index, parent_row, parent_id)
                if data_group:
                    container = self.data_group_model_controller.get_data_group_of_parent(parent_row, parent_id)
                    container = container if container else self.document_controller.document_model
                    self.document_controller.remove_data_group_from_container(data_group, container)
            return False

        self.data_group_widget = ui.create_tree_widget()
        self.data_group_widget.item_model_controller = self.data_group_model_controller.item_model_controller
        self.data_group_widget.on_selection_changed = data_group_widget_selection_changed
        self.data_group_widget.on_item_key_pressed = data_group_widget_key_pressed
        self.data_group_widget.on_focus_changed = lambda focused: self.__set_focused(focused)

        # this message is received when the current item changes in the widget
        def data_item_widget_selection_changed(indexes):
            if not self.__block1:
                if len(indexes) == 1:
                    # check the proper index; there are some cases where it gets out of sync
                    data_item = self.data_item_model_controller.get_data_item_by_index(indexes[0])
                else:
                    # nothing or multiple items selected
                    data_item = None
                self.__selection = DataPanelSelection(self.__selection.data_group, data_item, self.__selection.filter_id)
                if self.focused:
                    self.document_controller.set_selected_data_item(data_item)
                self.save_state()

        def data_item_widget_key_pressed(indexes, key):
            if key.is_delete:
                data_items = [self.data_item_model_controller.get_data_item_by_index(index) for index in indexes]
                if len(data_items):
                    for data_item in data_items:
                        self.data_item_model_controller.remove_data_item(data_item)
            return False

        def data_item_double_clicked(index):
            data_item = self.data_item_model_controller.get_data_item_by_index(index)
            if data_item:
                self.document_controller.new_window("data", DataPanelSelection(self.__selection.data_group, data_item, self.__selection.filter_id))

        self.data_item_widget = ui.create_list_widget(properties={"min-height": 240})
        self.data_item_widget.selection_mode = "extended"
        self.data_item_widget.list_model_controller = self.data_item_model_controller.list_model_controller
        self.data_item_widget.on_paint = lambda dc, options: self.data_item_model_controller.paint(dc, options)
        self.data_item_widget.on_selection_changed = data_item_widget_selection_changed
        self.data_item_widget.on_key_pressed = data_item_widget_key_pressed
        self.data_item_widget.on_item_double_clicked = data_item_double_clicked
        self.data_item_widget.on_focus_changed = lambda focused: self.__set_focused(focused)

        library_label_row = ui.create_row_widget()
        library_label = ui.create_label_widget(_("Library"), properties={"stylesheet": "font-weight: bold"})
        library_label_row.add_spacing(8)
        library_label_row.add(library_label)
        library_label_row.add_stretch()

        collections_label_row = ui.create_row_widget()
        collections_label = ui.create_label_widget(_("Collections"), properties={"stylesheet": "font-weight: bold"})
        collections_label_row.add_spacing(8)
        collections_label_row.add(collections_label)
        collections_label_row.add_stretch()

        def create_list_item_widget(ui, item):
            properties = {"stylesheet": "color: white; background-color: #3875D6;"} if item != "All" else None
            column = ui.create_column_widget(properties=properties)
            row = ui.create_row_widget()
            row.add_spacing(25)
            row.add(ui.create_label_widget(unicode(item)))
            row.add_stretch()
            column.add_spacing(1)
            column.add(row)
            column.add_spacing(1)
            return column

        class StringListBinding(Binding.Binding):
            def __init__(self, items):
                super(StringListBinding, self).__init__(None)
                self.items = items

        library_section_widget = ui.create_column_widget()
        library_section_widget.add_spacing(4)
        library_section_widget.add(library_label_row)
        library_section_widget.add(self.library_widget)

        collections_section_widget = ui.create_column_widget()
        collections_section_widget.add_spacing(4)
        collections_section_widget.add(collections_label_row)
        collections_section_widget.add(self.data_group_widget)

        self.master_widget = ui.create_column_widget()
        self.master_widget.add(library_section_widget)
        self.master_widget.add(collections_section_widget)
        self.master_widget.add_stretch()

        self.splitter = ui.create_splitter_widget("vertical", properties)
        self.splitter.orientation = "vertical"
        self.splitter.add(self.master_widget)
        self.splitter.add(self.data_item_widget)
        self.splitter.restore_state("window/v1/data_panel_splitter")

        self.widget = self.splitter

        # connect self as listener. this will result in calls to update_data_panel_selection
        self.document_controller.add_listener(self)
        self.document_controller.weak_data_panel = weakref.ref(self)

        # restore selection
        self.restore_state()

    def close(self):
        self.__closing = True
        self.splitter.save_state("window/v1/data_panel_splitter")
        self.update_data_panel_selection(DataPanelSelection())
        # close the models
        self.data_item_model_controller.close()
        self.data_group_model_controller.close()
        self.library_model_controller.close()
        # disconnect self as listener
        self.document_controller.weak_data_panel = None
        self.document_controller.remove_listener(self)
        # finish closing
        super(DataPanel, self).close()

    def periodic(self):
        super(DataPanel, self).periodic()
        self.data_item_model_controller.periodic()
        self.library_model_controller.periodic()

    def restore_state(self):
        data_group_uuid_str = self.ui.get_persistent_string("selected_data_group")
        data_item_uuid_str = self.ui.get_persistent_string("selected_data_item")
        filter_id = self.ui.get_persistent_string("selected_filter_id")
        data_group_uuid = uuid.UUID(data_group_uuid_str) if data_group_uuid_str else None
        data_item_uuid = uuid.UUID(data_item_uuid_str) if data_item_uuid_str else None
        data_group = self.document_controller.document_model.get_data_group_by_uuid(data_group_uuid)
        data_item = self.document_controller.document_model.get_data_item_by_uuid(data_item_uuid)
        self.update_data_panel_selection(DataPanelSelection(data_group, data_item, filter_id))

    def save_state(self):
        if not self.__closing:
            data_panel_selection = self.__selection
            if data_panel_selection.data_group:
                self.ui.set_persistent_string("selected_data_group", str(data_panel_selection.data_group.uuid))
            else:
                self.ui.remove_persistent_key("selected_data_group")
            if data_panel_selection.data_item:
                self.ui.set_persistent_string("selected_data_item", str(data_panel_selection.data_item.uuid))
            else:
                self.ui.remove_persistent_key("selected_data_item")
            if data_panel_selection.filter_id:
                self.ui.set_persistent_string("selected_filter_id", str(data_panel_selection.filter_id))
            else:
                self.ui.remove_persistent_key("selected_filter_id")

    # the focused property gets set from on_focus_changed on the data item widget. when gaining focus,
    # make sure the document controller knows what is selected so it can update the inspector.
    def __get_focused(self):
        return self.__focused
    def __set_focused(self, focused):
        self.__focused = focused
        if not self.__closing:
            self.document_controller.set_selected_data_item(self.__selection.data_item)
    focused = property(__get_focused, __set_focused)

    def __get_data_item(self):
        return self.__selection.data_item
    data_item = property(__get_data_item)

    # if the data_panel_selection gets changed, the data group tree and data item list need
    # to be updated to reflect the new selection. care needs to be taken to not introduce
    # update cycles.
    # three areas where this method is used are when starting acquisition, when quitting and
    # restarting, and after adding an operation to a data item.
    # not thread safe.
    def update_data_panel_selection(self, data_panel_selection):
        # block. why? so we don't get infinite loops.
        saved_block1 = self.__block1
        self.__block1 = True
        data_group = data_panel_selection.data_group
        data_item = data_panel_selection.data_item
        filter_id = data_panel_selection.filter_id
        # first select the right row in the library or data group widget
        if data_group:
            index, parent_row, parent_id = self.data_group_model_controller.get_data_group_index(data_group)
            self.library_widget.clear_current_row()
            self.data_group_widget.set_current_row(index, parent_row, parent_id)
        else:
            self.data_group_widget.clear_current_row()
            if filter_id == "latest-session":
                self.library_widget.set_current_row(1, -1, 0)
            else:
                self.library_widget.set_current_row(0, -1, 0)
        # update the data group that the data item model is tracking
        self.document_controller.set_data_group_or_filter(data_group, filter_id)
        self.periodic()  # ugh. sync the update so that it occurs before setting the index below.
        # update the data item selection
        #self.periodic()  # in order to update the selection, must make sure the model is updated. this is ugly.
        self.data_item_widget.current_index = self.data_item_model_controller.get_data_item_index(data_item)
        self.__selection = data_panel_selection
        # save the users selection
        self.save_state()
        # unblock
        self.__block1 = saved_block1

    # not thread safe
    def update_data_item_selection(self, data_item, source_data_item=None):
        # never change the selected data group or filter. however, if the data item appears in the list, select it
        if data_item in self.data_item_model_controller.data_items:
            self.update_data_panel_selection(DataPanelSelection(self.__selection.data_group, data_item, self.__selection.filter_id))

    def library_model_receive_files(self, file_paths, index, external=False, threaded=True):
        def receive_files_complete(received_data_items):
            def select_library_all():
                self.update_data_panel_selection(DataPanelSelection(data_item=received_data_items[0]))
            if len(received_data_items) > 0:
                self.queue_task(select_library_all)
        self.document_controller.receive_files(file_paths, None, index, external, threaded, receive_files_complete)
        return True

    # receive files dropped into the data group. default is to embed files (external=True), not link.
    # this message comes from the data group model, which is why it is named the way it is.
    def data_group_model_receive_files(self, file_paths, data_group, index, external=False, threaded=True):
        def receive_files_complete(received_data_items):
            def select_data_group_and_data_item():
                self.update_data_panel_selection(DataPanelSelection(data_group=data_group, data_item=received_data_items[0]))
            if len(received_data_items) > 0:
                if threaded:
                    self.queue_task(select_data_group_and_data_item)
                else:
                    select_data_group_and_data_item()
        self.document_controller.receive_files(file_paths, data_group, index, external, threaded, receive_files_complete)
        return True

# standard libraries
import copy
import functools
import logging
import pathlib
import typing
import uuid
import weakref

# local libraries
from nion.swift.model import Changes
from nion.swift.model import Connection
from nion.swift.model import Symbolic
from nion.swift.model import DataItem
from nion.swift.model import DataStructure
from nion.swift.model import DisplayItem
from nion.swift.model import FileStorageSystem
from nion.utils import ListModel
from nion.utils import Observable
from nion.utils import Persistence


ProjectItemType = typing.Union[DataItem.DataItem, DisplayItem.DisplayItem, DataStructure.DataStructure, Connection.Connection, Symbolic.Computation]


class Project(Observable.Observable, Persistence.PersistentObject):
    """A project manages raw data items, display items, computations, data structures, and connections.

    Projects are stored in project indexes, which are files that describe how to find data and and tracks the other
    project relationships (display items, computations, data structures, connections).

    Projects manage reading, writing, and data migration.
    """

    PROJECT_VERSION = 3

    def __init__(self, storage_system: FileStorageSystem.ProjectStorageSystem, project_reference: typing.Dict):
        super().__init__()

        self.__container_weak_ref = None

        self.define_type("project")
        self.define_relationship("data_items", data_item_factory, insert=self.__data_item_inserted, remove=self.__data_item_removed)
        self.define_relationship("display_items", display_item_factory, insert=self.__display_item_inserted, remove=self.__display_item_removed)
        self.define_relationship("computations", computation_factory, insert=self.__computation_inserted, remove=self.__computation_removed)
        self.define_relationship("data_structures", data_structure_factory, insert=self.__data_structure_inserted, remove=self.__data_structure_removed)
        self.define_relationship("connections", Connection.connection_factory, insert=self.__connection_inserted, remove=self.__connection_removed)

        self.__project_reference = copy.deepcopy(project_reference)
        self.__project_state = None
        self.__project_version = 0

        self._raw_properties = None  # debugging

        self.__storage_system = storage_system

        self.set_storage_system(self.__storage_system)

    def open(self) -> None:
        self.__storage_system.reset()  # this makes storage reusable during tests

    def close(self):
        for data_item in self.data_items:
            data_item.about_to_close()
        for data_item in self.data_items:
            data_item.about_to_be_removed()
        for data_item in self.data_items:
            data_item.close()
        self.__container_weak_ref = None

    @property
    def container(self):
        return self.__container_weak_ref() if self.__container_weak_ref else None

    def about_to_be_inserted(self, container):
        assert self.__container_weak_ref is None
        self.__container_weak_ref = weakref.ref(container)

    def about_to_be_removed(self):
        # called before close and before item is removed from its container
        self.about_to_be_removed_event.fire()
        assert not self._about_to_be_removed
        self._about_to_be_removed = True

    def insert_model_item(self, container, name, before_index, item):
        """Insert a model item. Let this item's container do it if possible; otherwise do it directly.

        Passing responsibility to this item's container allows the library to easily track dependencies.
        However, if this item isn't yet in the library hierarchy, then do the operation directly.
        """
        if self.__container_weak_ref:
            self.container.insert_model_item(container, name, before_index, item)
        else:
            container.insert_item(name, before_index, item)

    def remove_model_item(self, container, name, item, *, safe: bool=False) -> Changes.UndeleteLog:
        """Remove a model item. Let this item's container do it if possible; otherwise do it directly.

        Passing responsibility to this item's container allows the library to easily track dependencies.
        However, if this item isn't yet in the library hierarchy, then do the operation directly.
        """
        if self.__container_weak_ref:
            return self.container.remove_model_item(container, name, item, safe=safe)
        else:
            container.remove_item(name, item)
            return Changes.UndeleteLog()

    def _get_related_item(self, item_uuid: uuid.UUID) -> typing.Optional[Persistence.PersistentObject]:
        for data_item in self.data_items:
            if data_item.uuid == item_uuid:
                return data_item
        for display_item in self.display_items:
            if display_item.uuid == item_uuid:
                return display_item
            for display_data_channel in display_item.display_data_channels:
                if display_data_channel.uuid == item_uuid:
                    return display_data_channel
        for connection in self.connections:
            if connection.uuid == item_uuid:
                return connection
        for data_structure in self.data_structures:
            if data_structure.uuid == item_uuid:
                return data_structure
        for computation in self.computations:
            if computation.uuid == item_uuid:
                return computation
        item = super()._get_related_item(item_uuid)
        # if item and get_project_for_item(item) != self:
        #     print(f"!! project {self} {type(item)} {id(item)} {item.uuid}")
        return item

    @property
    def needs_upgrade(self) -> bool:
        return self.__project_reference.get("type") == "project_folder"

    @property
    def project_reference(self) -> typing.Dict:
        return copy.deepcopy(self.__project_reference)

    @property
    def project_reference_parts(self) -> typing.Tuple[str]:
        if self.__project_reference.get("type") == "project_folder":
            return pathlib.Path(self.__storage_system.get_identifier()).parent.parts
        else:
            return pathlib.Path(self.__storage_system.get_identifier()).parts

    @property
    def legacy_path(self) -> pathlib.Path:
        return pathlib.Path(self.__storage_system.get_identifier()).parent

    @property
    def project_state(self) -> str:
        return self.__project_state

    @property
    def project_version(self) -> int:
        return self.__project_version

    @property
    def project_title(self) -> str:
        return pathlib.Path(self.project_reference_parts[-1]).stem

    @property
    def project_filter(self) -> ListModel.Filter:

        def is_display_item_active(project_weak_ref, display_item: DisplayItem.DisplayItem) -> bool:
            return display_item in project_weak_ref().display_items

        # use a weak reference to avoid circular references loops that prevent garbage collection
        return ListModel.PredicateFilter(functools.partial(is_display_item_active, weakref.ref(self)))

    @property
    def project_storage_system(self) -> FileStorageSystem.ProjectStorageSystem:
        return self.__storage_system

    def __data_item_inserted(self, name: str, before_index: int, data_item: DataItem.DataItem) -> None:
        data_item.about_to_be_inserted(self)
        self.notify_insert_item("data_items", data_item, before_index)

    def __data_item_removed(self, name: str, index: int, data_item: DataItem.DataItem) -> None:
        data_item.about_to_be_removed()
        self.notify_remove_item("data_items", data_item, index)

    def __display_item_inserted(self, name: str, before_index: int, display_item: DisplayItem.DisplayItem) -> None:
        display_item.about_to_be_inserted(self)
        self.notify_insert_item("display_items", display_item, before_index)

    def __display_item_removed(self, name: str, index: int, display_item: DisplayItem.DisplayItem) -> None:
        display_item.about_to_be_removed()
        self.notify_remove_item("display_items", display_item, index)

    def __data_structure_inserted(self, name: str, before_index: int, data_structure: DataStructure.DataStructure) -> None:
        data_structure.about_to_be_inserted(self)
        self.notify_insert_item("data_structures", data_structure, before_index)

    def __data_structure_removed(self, name: str, index: int, data_structure: DataStructure.DataStructure) -> None:
        data_structure.about_to_be_removed()
        self.notify_remove_item("data_structures", data_structure, index)

    def __computation_inserted(self, name: str, before_index: int, computation: Symbolic.Computation) -> None:
        computation.about_to_be_inserted(self)
        self.notify_insert_item("computations", computation, before_index)

    def __computation_removed(self, name: str, index: int, computation: Symbolic.Computation) -> None:
        computation.about_to_be_removed()
        self.notify_remove_item("computations", computation, index)

    def __connection_inserted(self, name: str, before_index: int, connection: Connection.Connection) -> None:
        connection.about_to_be_inserted(self)
        self.notify_insert_item("connections", connection, before_index)

    def __connection_removed(self, name: str, index: int, connection: Connection.Connection) -> None:
        connection.about_to_be_removed()
        self.notify_remove_item("connections", connection, index)

    def _get_relationship_persistent_dict(self, item, key: str, index: int) -> typing.Dict:
        if key == "data_items":
            return self.__storage_system.get_persistent_dict("data_items", item.uuid)
        else:
            return super()._get_relationship_persistent_dict(item, key, index)

    def _get_relationship_persistent_dict_by_uuid(self, item, key: str) -> typing.Optional[typing.Dict]:
        if key == "data_items":
            return self.__storage_system.get_persistent_dict("data_items", item.uuid)
        else:
            return super()._get_relationship_persistent_dict_by_uuid(item, key)

    def read_project(self) -> None:
        # first read the library (for deletions) and the library items from the primary storage systems
        logging.getLogger("loader").info(f"Loading project {self.__storage_system.get_identifier()}")
        properties = self.__storage_system.read_project_properties()  # combines library and data item properties
        self.__project_version = properties.get("version", None)
        if self.__project_version is not None and self.__project_version in (FileStorageSystem.PROJECT_VERSION, 2):
            for item_d in properties.get("data_items", list()):
                data_item = DataItem.DataItem()
                data_item.begin_reading()
                data_item.read_from_dict(item_d)
                data_item.finish_reading()
                if data_item.uuid not in {data_item.uuid for data_item in self.data_items}:
                    self.load_item("data_items", len(self.data_items), data_item)
            for item_d in properties.get("display_items", list()):
                display_item = DisplayItem.DisplayItem()
                display_item.begin_reading()
                display_item.read_from_dict(item_d)
                display_item.finish_reading()
                if not display_item.uuid in {display_item.uuid for display_item in self.display_items}:
                    self.load_item("display_items", len(self.display_items), display_item)
            for item_d in properties.get("data_structures", list()):
                data_structure = DataStructure.DataStructure()
                data_structure.begin_reading()
                data_structure.read_from_dict(item_d)
                data_structure.finish_reading()
                if not data_structure.uuid in {data_structure.uuid for data_structure in self.data_structures}:
                    self.load_item("data_structures", len(self.data_structures), data_structure)
            for item_d in properties.get("computations", list()):
                computation = Symbolic.Computation()
                computation.begin_reading()
                computation.read_from_dict(item_d)
                computation.finish_reading()
                if not computation.uuid in {computation.uuid for computation in self.computations}:
                    self.load_item("computations", len(self.computations), computation)
                    # TODO: handle update script and bind after reload in document model
                    computation.update_script(self.container.container._processing_descriptions)
            for item_d in properties.get("connections", list()):
                connection = Connection.connection_factory(item_d.get)
                connection.begin_reading()
                connection.read_from_dict(item_d)
                connection.finish_reading()
                if not connection.uuid in {connection.uuid for connection in self.connections}:
                    self.load_item("connections", len(self.connections), connection)
            self.__project_state = "loaded"
        elif self.__project_version is not None:
            self.__project_state = "needs_upgrade"
        else:
            self.__project_state = "missing"
        self._raw_properties = properties

    def append_data_item(self, data_item: DataItem.DataItem) -> None:
        assert data_item.uuid not in {data_item.uuid for data_item in self.data_items}
        self.append_item("data_items", data_item)
        data_item.write_data_if_not_delayed()  # initially write to disk

    def remove_data_item(self, data_item: DataItem.DataItem) -> None:
        self.remove_item("data_items", data_item)

    def restore_data_item(self, data_item_uuid: uuid.UUID) -> typing.Optional[DataItem.DataItem]:
        item_d = self.__storage_system.restore_item(data_item_uuid)
        if item_d is not None:
            data_item_uuid = uuid.UUID(item_d.get("uuid"))
            large_format = item_d.get("__large_format", False)
            data_item = DataItem.DataItem(item_uuid=data_item_uuid, large_format=large_format)
            data_item.begin_reading()
            data_item.read_from_dict(item_d)
            data_item.finish_reading()
            assert data_item.uuid not in {data_item.uuid for data_item in self.data_items}
            self.append_item("data_items", data_item)
            assert data_item.container == self
            assert get_project_for_item(data_item) == self
            return data_item
        return None

    def append_display_item(self, display_item: DisplayItem.DisplayItem) -> None:
        assert display_item.uuid not in {display_item.uuid for display_item in self.display_items}
        self.append_item("display_items", display_item)

    def remove_display_item(self, display_item: DisplayItem.DisplayItem) -> None:
        self.remove_item("display_items", display_item)

    def append_data_structure(self, data_structure: DataStructure.DataStructure) -> None:
        assert data_structure.uuid not in {data_structure.uuid for data_structure in self.data_structures}
        self.append_item("data_structures", data_structure)

    def remove_data_structure(self, data_structure: DataStructure.DataStructure) -> None:
        self.remove_item("data_structures", data_structure)

    def append_computation(self, computation: Symbolic.Computation) -> None:
        assert computation.uuid not in {computation.uuid for computation in self.computations}
        self.append_item("computations", computation)

    def remove_computation(self, computation: Symbolic.Computation) -> None:
        self.remove_item("computations", computation)

    def append_connection(self, connection: Connection.Connection) -> None:
        assert connection.uuid not in {connection.uuid for connection in self.connections}
        self.append_item("connections", connection)

    def remove_connection(self, connection: Connection.Connection) -> None:
        self.remove_item("connections", connection)

    def prune(self) -> None:
        self.__storage_system.prune()

    def migrate_to_latest(self) -> None:
        self.__storage_system.migrate_to_latest()
        self.__storage_system.load_properties()
        self.update_storage_system()  # reload the properties
        self.read_project()

    def unmount(self) -> None:
        while len(self.connections) > 0:
            self.unload_item("connections", len(self.connections) - 1)
        while len(self.computations) > 0:
            self.unload_item("computations", len(self.computations) - 1)
        while len(self.data_structures) > 0:
            self.unload_item("data_structures", len(self.data_structures) - 1)
        while len(self.display_items) > 0:
            self.unload_item("display_items", len(self.display_items) - 1)
        while len(self.data_items) > 0:
            self.unload_item("data_items", len(self.data_items) - 1)


def data_item_factory(lookup_id):
    data_item_uuid = uuid.UUID(lookup_id("uuid"))
    large_format = lookup_id("__large_format", False)
    return DataItem.DataItem(item_uuid=data_item_uuid, large_format=large_format)


def display_item_factory(lookup_id):
    display_item_uuid = uuid.UUID(lookup_id("uuid"))
    return DisplayItem.DisplayItem(item_uuid=display_item_uuid)


def computation_factory(lookup_id):
    return Symbolic.Computation()


def data_structure_factory(lookup_id):
    return DataStructure.DataStructure()


def make_project(profile_context, project_reference: typing.Dict) -> typing.Optional[Project]:
    project_storage_system = FileStorageSystem.make_storage_system(profile_context, project_reference)
    project_storage_system.load_properties()
    if project_storage_system:
        return Project(project_storage_system, project_reference)
    return None


def get_project_for_item(item) -> typing.Optional[Project]:
    if item:
        if isinstance(item, Project):
            return item
        return get_project_for_item(item.container)
    return None

# standard libraries
import asyncio
import copy
import datetime
import gettext
import json
import logging
import os
import pathlib
import sys
import typing

# third party libraries
# None

# local libraries
from nion.swift import DataPanel
from nion.swift import DocumentController
from nion.swift import FilterPanel
from nion.swift import HistogramPanel
from nion.swift import InfoPanel
from nion.swift import Inspector
from nion.swift import MetadataPanel
from nion.swift import Panel
from nion.swift import SessionPanel
from nion.swift import Task
from nion.swift import Test
from nion.swift import ToolbarPanel
from nion.swift import Workspace
from nion.swift.model import ApplicationData
from nion.swift.model import Cache
from nion.swift.model import ColorMaps
from nion.swift.model import DocumentModel
from nion.swift.model import HardwareSource
from nion.swift.model import PlugInManager
from nion.swift.model import Profile
from nion.swift.model import Utility
from nion.ui import Application as UIApplication
from nion.ui import Dialog
from nion.ui import Widgets
from nion.utils import Event
from nion.utils import Process
from nion.utils import Selection

_ = gettext.gettext

app = None


# facilitate bootstrapping the application
class Application(UIApplication.Application):

    def __init__(self, ui, set_global=True, resources_path=None):
        super().__init__(ui)

        logging.getLogger("migration").setLevel(logging.ERROR)

        global app

        ui.set_application_info("Nion Swift", "Nion", "nion.com")

        self.ui.persistence_root = "3"  # sets of preferences
        self.__resources_path = resources_path
        self.version_str = "0.14.8"

        self.document_model_available_event = Event.Event()

        self.__event_loop = None

        if True or set_global:
            app = self  # hack to get the single instance set. hmm. better way?

        self.__document_model = None

        # a list of document controllers in the application.
        self.__document_controllers = []
        self.__menu_handlers = []

        # map these document controller events to listener tokens.
        # when the document controller closes, remove its listeners and
        # then remove it from the list of document controllers.
        # when the document controller requests a new document controller,
        # respond in this class by creating a new document controller.
        self.__did_close_event_listeners = dict()
        self.__create_new_event_listeners = dict()

        workspace_manager = Workspace.WorkspaceManager()
        workspace_manager.register_panel(SessionPanel.SessionPanel, "session-panel", _("Session"), ["left", "right"], "right", {"min-width": 320, "height": 80})
        workspace_manager.register_panel(DataPanel.DataPanel, "data-panel", _("Data Panel"), ["left", "right"], "left", {"min-width": 320, "height": 400})
        workspace_manager.register_panel(HistogramPanel.HistogramPanel, "histogram-panel", _("Histogram"), ["left", "right"], "right", {"min-width": 320, "height": 140})
        workspace_manager.register_panel(InfoPanel.InfoPanel, "info-panel", _("Info"), ["left", "right"], "right", {"min-width": 320, "height": 60})
        workspace_manager.register_panel(Inspector.InspectorPanel, "inspector-panel", _("Inspector"), ["left", "right"], "right", {"min-width": 320})
        workspace_manager.register_panel(Task.TaskPanel, "task-panel", _("Task Panel"), ["left", "right"], "right", {"min-width": 320})
        workspace_manager.register_panel(Panel.OutputPanel, "output-panel", _("Output"), ["bottom"], "bottom", {"min-width": 480, "min-height": 200})
        workspace_manager.register_panel(ToolbarPanel.ToolbarPanel, "toolbar-panel", _("Toolbar"), ["top"], "top", {"height": 30})
        workspace_manager.register_panel(MetadataPanel.MetadataPanel, "metadata-panel", _("Metadata"), ["left", "right"], "right", {"width": 320, "height": 8})
        workspace_manager.register_filter_panel(FilterPanel.FilterPanel)

    def initialize(self, *, load_plug_ins=True, use_root_dir=True):
        # configure the event loop object
        logger = logging.getLogger()
        old_level = logger.level
        logger.setLevel(logging.INFO)
        self.__event_loop = asyncio.new_event_loop()  # outputs a debugger message!
        logger.setLevel(old_level)
        # configure app data
        if load_plug_ins:
            logging.info("Python version " + str(sys.version.replace('\n', '')))
            logging.info("User interface class " + type(self.ui).__name__ + " / " + type(self.ui.proxy).__name__)
            app_data_file_path = self.ui.get_configuration_location() / pathlib.Path("nionswift_appdata.json")
            ApplicationData.set_file_path(app_data_file_path)
            logging.info("Application data: " + str(app_data_file_path))
            PlugInManager.load_plug_ins(self, get_root_dir() if use_root_dir else None)
            color_maps_dir = self.ui.get_configuration_location() / pathlib.Path("Color Maps")
            if color_maps_dir.exists():
                logging.info("Loading color maps from " + str(color_maps_dir))
                ColorMaps.load_color_maps(color_maps_dir)
            else:
                logging.info("NOT Loading color maps from " + str(color_maps_dir) + " (missing)")

    def deinitialize(self):
        # shut down hardware source manager, unload plug-ins, and really exit ui
        HardwareSource.HardwareSourceManager().close()
        PlugInManager.unload_plug_ins()
        with open(os.path.join(self.ui.get_data_location(), "PythonConfig.ini"), 'w') as f:
            f.write(sys.prefix + '\n')
        Process.close_event_loop(self.__event_loop)
        self.__event_loop = None
        self.ui.close()

    def run(self):
        """Alternate start which allows ui to control event loop."""
        self.ui.run(self)

    def exit(self):
        # close all document windows
        for document_controller in copy.copy(self.__document_controllers):
            # closing the document window will trigger the about_to_close event to be called which
            # will then call document controller close which will fire its did_close_event which will
            # remove the document controller from the list of document controllers.
            document_controller.request_close()
        # document model is reference counted; when the no document controller holds a reference to the
        # document model, it will be closed.

    def periodic(self) -> None:
        if self.__event_loop:  # special for shutdown
            self.__event_loop.stop()
            self.__event_loop.run_forever()

    @property
    def event_loop(self) -> asyncio.AbstractEventLoop:
        return self.__event_loop

    @property
    def document_model(self):
        return self.__document_model

    # for testing
    def _set_document_model(self, document_model):
        self.__document_model = document_model

    def start(self, *, profile_dir: pathlib.Path = None):
        """
            Start the application.

            Looks for workspace_location persistent string. If it doesn't find it, uses a default
            workspace location.

            Then checks to see if that workspace exists. If not and if skip_choose has not been
            set to True, asks the user for a workspace location. User may choose new folder or
            existing location. This works by putting up the dialog which will either call start
            again or exit.

            Creates workspace in location if it doesn't exist.

            Migrates database to latest version.

            Creates document model, resources path, etc.
        """
        logging.getLogger("migration").setLevel(logging.INFO)
        if profile_dir:
            profile_path = profile_dir / pathlib.Path("Profile").with_suffix("nsproj")
        else:
            data_dir = pathlib.Path(self.ui.get_data_location())
            profile_name = pathlib.Path(self.ui.get_persistent_string("profile_name", "Profile"))
            profile_path = data_dir / profile_name.with_suffix("nsproj")
        welcome_message_enabled = profile_dir is None
        profile, is_created = Profile.create_profile(profile_path, welcome_message_enabled)
        DocumentModel.DocumentModel.computation_min_period = 0.1
        DocumentModel.DocumentModel.computation_min_factor = 1.0
        document_model = DocumentModel.DocumentModel(profile=profile)
        document_model.create_default_data_groups()
        document_model.start_dispatcher()
        # create the document controller
        document_controller = self.create_document_controller(document_model, "library")
        if welcome_message_enabled:
            logging.info("Welcome to Nion Swift.")
        if is_created and len(document_model.display_items) > 0:
            document_controller.selected_display_panel.set_display_panel_display_item(document_model.display_items[0])
            document_controller.selected_display_panel.perform_action("set_fill_mode")
        return True

    def stop(self):
        # program is really stopping, clean up.
        self.deinitialize()

    def get_recent_workspace_file_paths(self):
        workspace_history = self.ui.get_persistent_object("workspace_history", list())
        return [file_path for file_path in workspace_history if os.path.exists(file_path)]

    def create_document_controller(self, document_model, workspace_id, display_item=None):
        self._set_document_model(document_model)  # required to allow API to find document model
        document_controller = DocumentController.DocumentController(self.ui, document_model, workspace_id=workspace_id, app=self)
        self.__did_close_event_listeners[document_controller] = document_controller.did_close_event.listen(self.__document_controller_did_close)
        self.__create_new_event_listeners[document_controller] = document_controller.create_new_document_controller_event.listen(self.create_document_controller)
        self.__register_document_controller(document_controller)
        self.document_model_available_event.fire(document_model)
        # attempt to set data item / group
        if display_item:
            display_panel = document_controller.selected_display_panel
            if display_panel:
                display_panel.set_display_panel_display_item(display_item)
        document_controller.show()
        return document_controller

    def __document_controller_did_close(self, document_controller):
        self.__did_close_event_listeners[document_controller].close()
        del self.__did_close_event_listeners[document_controller]
        self.__create_new_event_listeners[document_controller].close()
        del self.__create_new_event_listeners[document_controller]
        self.__document_controllers.remove(document_controller)

    def __register_document_controller(self, document_controller: DocumentController.DocumentController) -> None:
        assert document_controller not in self.__document_controllers
        self.__document_controllers.append(document_controller)
        # when a document window is registered, tell the menu handlers
        for menu_handler in self.__menu_handlers:  # use 'handler' to avoid name collision
            menu_handler(document_controller)

    @property
    def document_controllers(self) -> typing.List[DocumentController.DocumentController]:
        return copy.copy(self.__document_controllers)

    def register_menu_handler(self, new_menu_handler):
        assert new_menu_handler not in self.__menu_handlers
        self.__menu_handlers.append(new_menu_handler)
        # when a menu handler is registered, let it immediately know about existing menu handlers
        for document_controller in self.__document_controllers:
            new_menu_handler(document_controller)
        # return the menu handler so that it can be used to unregister (think: lambda)
        return new_menu_handler

    def unregister_menu_handler(self, menu_handler):
        self.__menu_handlers.remove(menu_handler)

    @property
    def menu_handlers(self) -> typing.List:
        return copy.copy(self.__menu_handlers)

    def run_all_tests(self):
        Test.run_all_tests()


def get_root_dir():
    root_dir = os.path.dirname((os.path.dirname(os.path.abspath(__file__))))
    path_ascend_count = 2
    for i in range(path_ascend_count):
        root_dir = os.path.dirname(root_dir)
    return root_dir

# system imports
import code
import contextlib
import copy
import gettext
import io
import re
import rlcompleter
import sys
import typing

# local libraries
from nion.swift import Panel
from nion.swift.model import DocumentModel
from nion.ui import Dialog
from nion.ui import UserInterface
from nion.ui import Widgets

if typing.TYPE_CHECKING:
    from nion.swift import DocumentController

_ = gettext.gettext


class ConsoleWidgetStateController:
    delims = " \t\n`~!@#$%^&*()-=+[{]}\\|;:\'\",<>/?"

    def __init__(self, locals: dict):
        self.__incomplete = False

        self.__console = code.InteractiveConsole(locals)

        self.__history = list()
        self.__history_point = None
        self.__command_cache = (None, "") # Meaning of the tuple: (history_point where the command belongs, command)

    @staticmethod
    def get_common_prefix(l):
        if not l:
            return str()
        s1 = min(l)  # return the first, alphabetically
        s2 = max(l)  # the last
        # check common characters in between
        for i, c in enumerate(s1):
            if c != s2[i]:
                return s1[:i]
        return s1

    @staticmethod
    @contextlib.contextmanager
    def reassign_stdout(new_stdout, new_stderr):
        oldstdout, oldtsderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = new_stdout, new_stderr
        yield
        sys.stdout, sys.stderr = oldstdout, oldtsderr

    @property
    def incomplete(self):
        return self.__incomplete

    # interpretCommand is called from the intrinsic widget.
    def interpret_command(self, command: str):
        if command:
            self.__history.append(command)
        self.__history_point = None
        self.__command_cache = (None, "")
        output = io.StringIO()
        error = io.StringIO()
        with ConsoleWidgetStateController.reassign_stdout(output, error):
            self.__incomplete = self.__console.push(command)
        if error.getvalue():
            result =  error.getvalue()
            error_code = -1
        else:
            result = output.getvalue()
            error_code = 0
        return result, error_code

    def complete_command(self, command: str) -> typing.Tuple[str, typing.List[str]]:
        terms = list()
        completed_command = command
        completer = rlcompleter.Completer(namespace=self.__console.locals)
        index = 0
        rx = "([" + re.escape(ConsoleWidgetStateController.delims) + "])"
        # the parenthesis around rx make it a group. This will cause split to keep the characters in rx in the
        # list, so that we can reconstruct the original string later
        split_commands = re.split(rx, command)
        if len(split_commands) > 0:
            completion_term = split_commands[-1]
            while True:
                term = completer.complete(completion_term, index)
                if term is None:
                    break
                index += 1
                # for some reason rlcomplete returns "\t" when completing "", so exclude that case here
                if not term.startswith(completion_term + "__") and term != "\t":
                    terms.append(term)
            if len(terms) == 1:
                completed_command = command[:command.rfind(completion_term)] + terms[0]
                terms = list()
            elif len(terms) > 1:
                common_prefix = ConsoleWidgetStateController.get_common_prefix(terms)
                completed_command = "".join(split_commands[:-1]) + common_prefix

        return completed_command, terms

    def move_back_in_history(self, current_line: str) -> str:
        line = ""
        if self.__history_point is None:
            self.__history_point = len(self.__history)
            # do not update command_cache if the user didn't type anything
            if current_line:
                self.__command_cache = (None, current_line)
        elif self.__history_point < len(self.__history):
            # This means the user changed something at the current point in history. Save the temporary command.
            if current_line != self.__history[self.__history_point]:
                self.__command_cache = (self.__history_point, current_line)
        self.__history_point = max(0, self.__history_point - 1)
        if self.__history_point < len(self.__history):
            line = self.__command_cache[1] if self.__command_cache[0] == self.__history_point else self.__history[self.__history_point]

        return line

    def move_forward_in_history(self, current_line: str) -> str:
        line = ""
        if self.__history_point is not None:
            if self.__history_point < len(self.__history):
                # This means the user changed something at the current point in history.
                # Save the temporary command, but only if the user actually typed something
                if current_line and current_line != self.__history[self.__history_point]:
                    self.__command_cache = (self.__history_point, current_line)
            self.__history_point = min(len(self.__history), self.__history_point + 1)
            if self.__history_point < len(self.__history):
                line = self.__command_cache[1] if self.__command_cache[0] == self.__history_point else self.__history[self.__history_point]
            else:
                self.__history_point = None

        # Do not use 'else' here because history_point might have been set to 'None' in the first 'if' statement
        if self.__history_point is None:
            line = self.__command_cache[1] if self.__command_cache[0] is None else ""

        return line


class ConsoleWidget(Widgets.CompositeWidgetBase):

    def __init__(self, ui: UserInterface.UserInterface, locals=None, properties=None):
        super().__init__(ui.create_column_widget())

        self.prompt = ">>> "
        self.continuation_prompt = "... "

        self.__cursor_position = None

        self.__text_edit_widget = ui.create_text_edit_widget(properties)
        self.__text_edit_widget.set_text_color("white")
        self.__text_edit_widget.set_text_background_color("black")
        self.__text_edit_widget.set_text_font(Panel.Panel.get_monospace_text_font())
        self.__text_edit_widget.set_line_height_proportional(Panel.Panel.get_monospace_proportional_line_height())
        self.__text_edit_widget.word_wrap_mode = "anywhere"
        self.__text_edit_widget.on_cursor_position_changed = self.__cursor_position_changed
        self.__text_edit_widget.on_selection_changed = self.__selection_changed
        self.__text_edit_widget.on_return_pressed = self.__return_pressed
        self.__text_edit_widget.on_key_pressed = self.__key_pressed
        self.__text_edit_widget.on_insert_mime_data = self.__insert_mime_data

        class StdoutCatcher:
            def __init__(self, text_edit_widget):
                self.__text_edit_widget = text_edit_widget
            def write(self, stuff):
                if self.__text_edit_widget:
                    self.__text_edit_widget.append_text(str(stuff).rstrip())
            def flush(self):
                pass
        stdout = StdoutCatcher(self.__text_edit_widget)

        locals = locals if locals is not None else dict()
        locals.update({'__name__': None, '__console__': None, '__doc__': None, '_stdout': stdout})

        self.__state_controller = ConsoleWidgetStateController(locals)

        self.__text_edit_widget.append_text(self.prompt)
        self.__text_edit_widget.move_cursor_position("end")
        self.__last_position = copy.deepcopy(self.__cursor_position)

        self.content_widget.add(self.__text_edit_widget)

    def close(self):
        super().close()
        self.__text_edit_widget = None

    @property
    def current_prompt(self):
        return self.continuation_prompt if self.__state_controller.incomplete else self.prompt

    def insert_lines(self, lines):
        for l in lines:
            self.__text_edit_widget.move_cursor_position("end")
            self.__text_edit_widget.insert_text(l)
            result, error_code = self.__state_controller.interpret_command(l)
            if len(result) > 0:
                self.__text_edit_widget.set_text_color("red" if error_code else "green")
                self.__text_edit_widget.append_text(result[:-1])
                self.__text_edit_widget.set_text_color("white")
            self.__text_edit_widget.append_text(self.current_prompt)
            self.__text_edit_widget.move_cursor_position("end")
            self.__last_position = copy.deepcopy(self.__cursor_position)

    def interpret_lines(self, lines):
        for l in lines:
            self.__state_controller.interpret_command(l)

    def __return_pressed(self):
        command = self.__get_partial_command()
        result, error_code = self.__state_controller.interpret_command(command)
        if len(result) > 0:
            self.__text_edit_widget.set_text_color("red" if error_code else "green")
            self.__text_edit_widget.append_text(result[:-1])
        self.__text_edit_widget.set_text_color("white")
        self.__text_edit_widget.append_text(self.current_prompt)
        self.__text_edit_widget.move_cursor_position("end")
        self.__last_position = copy.deepcopy(self.__cursor_position)
        return True

    def __get_partial_command(self):
        command = self.__text_edit_widget.text.split('\n')[-1]
        if command.startswith(self.prompt):
            command = command[len(self.prompt):]
        elif command.startswith(self.continuation_prompt):
            command = command[len(self.continuation_prompt):]
        return command

    def __key_pressed(self, key):
        is_cursor_on_last_line = self.__cursor_position.block_number == self.__last_position.block_number
        partial_command = self.__get_partial_command()
        is_cursor_on_last_column = (partial_command.strip() and
                                    self.__cursor_position.column_number == len(self.current_prompt + partial_command))

        if is_cursor_on_last_line and key.is_up_arrow:
            line = self.__state_controller.move_back_in_history(partial_command)
            self.__text_edit_widget.move_cursor_position("start_para", "move")
            self.__text_edit_widget.move_cursor_position("end_para", "keep")
            self.__text_edit_widget.insert_text("{}{}".format(self.current_prompt, line))
            self.__text_edit_widget.move_cursor_position("end")
            self.__last_position = copy.deepcopy(self.__cursor_position)
            return True

        if is_cursor_on_last_line and key.is_down_arrow:
            line = self.__state_controller.move_forward_in_history(partial_command)
            self.__text_edit_widget.move_cursor_position("start_para", "move")
            self.__text_edit_widget.move_cursor_position("end_para", "keep")
            self.__text_edit_widget.insert_text("{}{}".format(self.current_prompt, line))
            self.__text_edit_widget.move_cursor_position("end")
            self.__last_position = copy.deepcopy(self.__cursor_position)
            return True

        if is_cursor_on_last_line and key.is_delete:
            if not partial_command:
                return True

        if is_cursor_on_last_line and key.is_move_to_start_of_line:
            mode = "keep" if key.modifiers.shift else "move"
            self.__text_edit_widget.move_cursor_position("start_para", mode)
            self.__text_edit_widget.move_cursor_position("next", mode, n=4)
            return True

        if is_cursor_on_last_line and key.is_delete_to_end_of_line:
            self.__text_edit_widget.move_cursor_position("end_para", "keep")
            self.__text_edit_widget.remove_selected_text()
            return True

        if is_cursor_on_last_line and key.key == 0x43 and key.modifiers.native_control and sys.platform == "darwin":
            self.__text_edit_widget.move_cursor_position("end")
            self.__text_edit_widget.insert_text("\n")
            self.__text_edit_widget.insert_text(self.current_prompt)
            self.__text_edit_widget.move_cursor_position("end")
            self.__last_position = copy.deepcopy(self.__cursor_position)
            return True

        if is_cursor_on_last_line and is_cursor_on_last_column and key.is_tab:
            completed_command, terms = self.__state_controller.complete_command(partial_command)
            if not terms:
                self.__text_edit_widget.move_cursor_position("start_para", "move")
                self.__text_edit_widget.move_cursor_position("end_para", "keep")
                self.__text_edit_widget.insert_text("{}{}".format(self.current_prompt, completed_command))
                self.__text_edit_widget.move_cursor_position("end")
                self.__last_position = copy.deepcopy(self.__cursor_position)
            elif len(terms) > 1:
                self.__text_edit_widget.move_cursor_position("end")
                self.__text_edit_widget.set_text_color("brown")
                self.__text_edit_widget.append_text("   ".join(terms) + "\n")
                self.__text_edit_widget.move_cursor_position("end")
                self.__text_edit_widget.set_text_color("white")
                self.__text_edit_widget.insert_text("{}{}".format(self.current_prompt, completed_command))
                self.__text_edit_widget.move_cursor_position("end")
                self.__last_position = copy.deepcopy(self.__cursor_position)
            return True
        return False

    def __cursor_position_changed(self, cursor_position):
        self.__cursor_position = copy.deepcopy(cursor_position)

    def __selection_changed(self, selection):
        pass

    def __insert_mime_data(self, mime_data):
        text = mime_data.data_as_string("text/plain")
        text_lines = re.split("[" + re.escape("\n") + re.escape("\r") + "]", text)
        if text_lines[-1] == "":
            text_lines = text_lines[:-1]
        if len(text_lines) == 1 and text_lines[0] == text.rstrip():
            # special case where text has no line terminator
            self.__text_edit_widget.insert_text(text)
        else:
            self.insert_lines(text_lines)



class ConsoleDialog(Dialog.ActionDialog):

    def __init__(self, document_controller: "DocumentController.DocumentController"):
        super().__init__(document_controller.ui, _("Python Console"), parent_window=document_controller, persistent_id="ConsoleDialog")

        self.__document_controller = document_controller

        self.__console_widget = ConsoleWidget(document_controller.ui, properties={"min-height": 180, "min-width": 540})

        lines = [
            "import logging",
            "import numpy as np",
            "import numpy as numpy",
            "import uuid",
            "from nion.swift.model import PlugInManager",
            "from nion.ui import Declarative",
            "from nion.data import xdata_1_0 as xd",
            "get_api = PlugInManager.api_broker_fn",
            "api = get_api('~1.0', '~1.0')",
            "ui = Declarative.DeclarativeUI()",
            "show = api.show",
            "def run_script(*args, **kwargs):",
            "  api.run_script(*args, stdout=_stdout, **kwargs)",
            "",
            ]

        variable_to_item_map = DocumentModel.MappedItemManager().item_map
        for variable_name, data_item in variable_to_item_map.items():
            data_item_specifier = data_item.item_specifier
            lines.append(f"{variable_name} = api.library.get_item_by_specifier(api.create_specifier(item_uuid=uuid.UUID('{str(data_item_specifier.item_uuid)}'), context_uuid=uuid.UUID('{str(data_item_specifier.context_uuid)}')))")

        self.__console_widget.interpret_lines(lines)

        self.content.add(self.__console_widget)

        self.__document_controller.register_console(self)

        self._create_menus()

    def close(self):
        self.__document_controller.unregister_console(self)
        super().close()

    def assign_data_item_var(self, data_item_var, data_item):
        self.__console_widget.insert_lines(["{} = api.library.get_data_item_by_uuid(uuid.UUID(\"{}\"))".format(data_item_var, data_item.uuid)])

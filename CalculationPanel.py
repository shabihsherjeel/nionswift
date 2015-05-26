# futures
from __future__ import absolute_import

# standard libraries
import gettext
import logging

# third party libraries
# None

# local libraries
from nion.swift.model import DataItem
from nion.swift.model import Symbolic
from nion.swift import Panel


_ = gettext.gettext


class CalculationPanel(Panel.Panel):

    def __init__(self, document_controller, panel_id, properties):
        super(CalculationPanel, self).__init__(document_controller, panel_id, _("Calculation"))

        ui = self.ui

        line_edit_row = ui.create_row_widget()
        line_edit = ui.create_line_edit_widget()
        line_edit.placeholder_text = _("Data Expression, e.g. ln(a) + b * 2")
        line_edit_row.add_spacing(8)
        line_edit_row.add(line_edit)
        line_edit_row.add_spacing(8)

        calculate_button = ui.create_push_button_widget(_("Calculate"))
        calculate_row = ui.create_row_widget()
        calculate_row.add_stretch()
        calculate_row.add(calculate_button)
        calculate_row.add_spacing(8)

        column = self.ui.create_column_widget()
        column.add_spacing(6)
        column.add(line_edit_row)
        column.add_spacing(6)
        column.add(calculate_row)
        column.add_spacing(6)

        def calculate():
            data_node = Symbolic.calculate(line_edit.text, self.document_controller.data_item_vars)
            if data_node:
                data = data_node.data
                if data is not None:
                    data_item = DataItem.DataItem(data)
                    self.document_controller.document_model.append_data_item(data_item)
                    self.document_controller.display_data_item(DataItem.DisplaySpecifier.from_data_item(data_item))
                    logging.info("Calculated %s", line_edit.text)
                else:
                    scalar = data_node.scalar if data_node else None
                    logging.info("%s <= %s", scalar, line_edit.text)
            line_edit.text = None

        def clear():
            line_edit.text = None

        line_edit.on_escape_pressed = clear
        line_edit.on_return_pressed = calculate
        calculate_button.on_clicked = calculate

        self.widget = column

    def close(self):
        super(CalculationPanel, self).close()

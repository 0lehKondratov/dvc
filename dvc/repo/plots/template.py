import json
import logging
import os
import re

from funcy import cached_property

from dvc.exceptions import DvcException
from dvc.utils.fs import makedirs

logger = logging.getLogger(__name__)


class TemplateNotFoundError(DvcException):
    def __init__(self, path):
        super().__init__(f"Template '{path}' not found.")


class NoDataForTemplateError(DvcException):
    def __init__(self, template_path):
        super().__init__(
            "No data provided for '{}'.".format(os.path.relpath(template_path))
        )


class NoFieldInDataError(DvcException):
    def __init__(self, field_name):
        super().__init__(
            f"Field '{field_name}' does not exist in provided data."
        )


class Template:
    INDENT = 4
    SEPARATORS = (",", ": ")
    EXTENSION = ".json"
    METRIC_DATA_ANCHOR = "<DVC_METRIC_DATA>"
    X_ANCHOR = "<DVC_METRIC_X>"
    Y_ANCHOR = "<DVC_METRIC_Y>"
    TITLE_ANCHOR = "<DVC_METRIC_TITLE>"
    X_LABEL_ANCHOR = "<DVC_METRIC_X_LABEL>"
    Y_LABEL_ANCHOR = "<DVC_METRIC_Y_LABEL>"

    def __init__(self, templates_dir):
        self.plot_templates_dir = templates_dir

    def dump(self):
        makedirs(self.plot_templates_dir, exist_ok=True)

        with open(
            os.path.join(
                self.plot_templates_dir, self.TEMPLATE_NAME + self.EXTENSION
            ),
            "w",
        ) as fobj:
            json.dump(
                self.DEFAULT_CONTENT,
                fobj,
                indent=self.INDENT,
                separators=self.SEPARATORS,
            )
            fobj.write("\n")

    @staticmethod
    def get_data_anchor(template_content):
        regex = re.compile('"<DVC_METRIC_DATA[^>"]*>"')
        return regex.findall(template_content)

    @staticmethod
    def parse_data_anchors(template_content):
        data_files = {
            Template.get_datafile(m)
            for m in Template.get_data_anchor(template_content)
        }
        return {df for df in data_files if df}

    @staticmethod
    def get_datafile(anchor_string):
        return (
            anchor_string.replace("<", "")
            .replace(">", "")
            .replace('"', "")
            .replace("DVC_METRIC_DATA", "")
            .replace(",", "")
        )

    @staticmethod
    def fill(
        template_path, data, priority_datafile=None, props=None,
    ):
        props = props or {}

        with open(template_path) as fobj:
            result_content = fobj.read()

        if props.get("x"):
            Template._check_field_exists(data, props.get("x"))
        if props.get("y"):
            Template._check_field_exists(data, props.get("y"))

        result_content = Template._replace_data_anchors(
            result_content, data, priority_datafile
        )

        result_content = Template._replace_metadata_anchors(
            result_content, props
        )

        return result_content

    @staticmethod
    def _check_field_exists(data, field):
        for file, data_points in data.items():
            if not any(
                field in data_point.keys() for data_point in data_points
            ):
                raise NoFieldInDataError(field)

    @staticmethod
    def _replace_metadata_anchors(result_content, props):
        props.setdefault("title", "")
        props.setdefault("x_label", props.get("x"))
        props.setdefault("y_label", props.get("y"))

        replace_pairs = [
            (Template.TITLE_ANCHOR, "title"),
            (Template.X_ANCHOR, "x"),
            (Template.Y_ANCHOR, "y"),
            (Template.X_LABEL_ANCHOR, "x_label"),
            (Template.Y_LABEL_ANCHOR, "y_label"),
        ]
        for anchor, key in replace_pairs:
            value = props.get(key)
            if anchor in result_content and value is not None:
                result_content = result_content.replace(anchor, value)

        return result_content

    @staticmethod
    def _replace_data_anchors(result_content, data, priority_datafile):
        for anchor in Template.get_data_anchor(result_content):
            file = Template.get_datafile(anchor)

            if not file or priority_datafile:
                key = priority_datafile
            else:
                key = file

            result_content = result_content.replace(
                anchor,
                json.dumps(
                    data[key],
                    indent=Template.INDENT,
                    separators=Template.SEPARATORS,
                    sort_keys=True,
                ),
            )
        return result_content


class DefaultLinearTemplate(Template):
    TEMPLATE_NAME = "default"

    DEFAULT_CONTENT = {
        "$schema": "https://vega.github.io/schema/vega-lite/v4.json",
        "data": {"values": Template.METRIC_DATA_ANCHOR},
        "title": Template.TITLE_ANCHOR,
        "mark": {"type": "line"},
        "encoding": {
            "x": {
                "field": Template.X_ANCHOR,
                "type": "quantitative",
                "title": Template.X_LABEL_ANCHOR,
            },
            "y": {
                "field": Template.Y_ANCHOR,
                "type": "quantitative",
                "title": Template.Y_LABEL_ANCHOR,
                "scale": {"zero": False},
            },
            "color": {"field": "rev", "type": "nominal"},
        },
    }


class DefaultConfusionTemplate(Template):
    TEMPLATE_NAME = "confusion"
    DEFAULT_CONTENT = {
        "$schema": "https://vega.github.io/schema/vega-lite/v4.json",
        "data": {"values": Template.METRIC_DATA_ANCHOR},
        "title": Template.TITLE_ANCHOR,
        "mark": "rect",
        "encoding": {
            "x": {
                "field": Template.X_ANCHOR,
                "type": "nominal",
                "sort": "ascending",
                "title": Template.X_LABEL_ANCHOR,
            },
            "y": {
                "field": Template.Y_ANCHOR,
                "type": "nominal",
                "sort": "ascending",
                "title": Template.Y_LABEL_ANCHOR,
            },
            "color": {"aggregate": "count", "type": "quantitative"},
            "facet": {"field": "rev", "type": "nominal"},
        },
    }


class DefaultScatterTemplate(Template):
    TEMPLATE_NAME = "scatter"
    DEFAULT_CONTENT = {
        "$schema": "https://vega.github.io/schema/vega-lite/v4.json",
        "data": {"values": Template.METRIC_DATA_ANCHOR},
        "title": Template.TITLE_ANCHOR,
        "mark": "point",
        "encoding": {
            "x": {
                "field": Template.X_ANCHOR,
                "type": "quantitative",
                "title": Template.X_LABEL_ANCHOR,
            },
            "y": {
                "field": Template.Y_ANCHOR,
                "type": "quantitative",
                "title": Template.Y_LABEL_ANCHOR,
                "scale": {"zero": False},
            },
            "color": {"field": "rev", "type": "nominal"},
        },
    }


class PlotTemplates:
    TEMPLATES_DIR = "plots"
    TEMPLATES = [
        DefaultLinearTemplate,
        DefaultConfusionTemplate,
        DefaultScatterTemplate,
    ]

    @cached_property
    def templates_dir(self):
        return os.path.join(self.dvc_dir, self.TEMPLATES_DIR)

    @cached_property
    def default_template(self):
        default_plot_path = os.path.join(self.templates_dir, "default.json")
        if not os.path.exists(default_plot_path):
            raise TemplateNotFoundError(os.path.relpath(default_plot_path))
        return default_plot_path

    def get_template(self, path):
        t_path = os.path.join(self.templates_dir, path)
        if os.path.exists(t_path):
            return t_path

        all_templates = [
            os.path.join(root, file)
            for root, _, files in os.walk(self.templates_dir)
            for file in files
        ]
        matches = [
            template
            for template in all_templates
            if os.path.splitext(template)[0] == t_path
        ]
        if matches:
            assert len(matches) == 1
            return matches[0]

        raise TemplateNotFoundError(path)

    def __init__(self, dvc_dir):
        self.dvc_dir = dvc_dir

        if not os.path.exists(self.templates_dir):
            makedirs(self.templates_dir, exist_ok=True)
            for t in self.TEMPLATES:
                t(self.templates_dir).dump()

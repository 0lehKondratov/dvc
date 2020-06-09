import json
import os

from funcy import cached_property

from dvc.exceptions import DvcException
from dvc.utils.fs import makedirs


class TemplateNotFoundError(DvcException):
    def __init__(self, path):
        super().__init__(f"Template '{path}' not found.")


class NoFieldInDataError(DvcException):
    def __init__(self, field_name):
        super().__init__(
            f"Field '{field_name}' does not exist in provided data."
        )


class Template:
    INDENT = 4
    SEPARATORS = (",", ": ")
    EXTENSION = ".json"
    ANCHOR = '"<DVC_METRIC_{}>"'
    METRIC_DATA_ANCHOR = "<DVC_METRIC_DATA>"
    X_ANCHOR = "<DVC_METRIC_X>"
    Y_ANCHOR = "<DVC_METRIC_Y>"
    TITLE_ANCHOR = "<DVC_METRIC_TITLE>"
    X_LABEL_ANCHOR = "<DVC_METRIC_X_LABEL>"
    Y_LABEL_ANCHOR = "<DVC_METRIC_Y_LABEL>"

    def __init__(self, content=None, name=None):
        self.content = self.DEFAULT_CONTENT if content is None else content
        self.name = name or self.DEFAULT_NAME
        self.filename = self.name + self.EXTENSION

    def render(self, data, props=None):
        props = props or {}

        if props.get("x"):
            Template._check_field_exists(data, props.get("x"))
        if props.get("y"):
            Template._check_field_exists(data, props.get("y"))

        content = self._fill_anchor(self.content, "data", data)
        content = self._fill_metadata(content, props)

        return content

    def has_anchor(self, name):
        return self._anchor(name) in self.content

    @classmethod
    def _fill_anchor(cls, content, name, value):
        value_str = json.dumps(
            value, indent=cls.INDENT, separators=cls.SEPARATORS, sort_keys=True
        )
        return content.replace(cls._anchor(name), value_str)

    @classmethod
    def _anchor(cls, name):
        return cls.ANCHOR.format(name.upper())

    @classmethod
    def _fill_metadata(cls, content, props):
        props.setdefault("title", "")
        props.setdefault("x_label", props.get("x"))
        props.setdefault("y_label", props.get("y"))

        names = ["title", "x", "y", "x_label", "y_label"]
        for name in names:
            value = props.get(name)
            if value is not None:
                content = cls._fill_anchor(content, name, value)

        return content

    @staticmethod
    def _check_field_exists(data, field):
        if not any(field in row for row in data):
            raise NoFieldInDataError(field)


class DefaultLinearTemplate(Template):
    DEFAULT_NAME = "default"

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
    DEFAULT_NAME = "confusion"
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
    DEFAULT_NAME = "scatter"
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
        if os.path.exists(path):
            return path

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
                self.dump(t())

    def dump(self, template):
        path = os.path.join(self.templates_dir, template.filename)
        with open(path, "w") as fd:
            json.dump(
                template.content,
                fd,
                indent=template.INDENT,
                separators=template.SEPARATORS,
            )
            fd.write("\n")

    def load(self, name):
        path = self.get_template(name)

        with open(path) as fd:
            content = fd.read()

        return Template(content, name=name)

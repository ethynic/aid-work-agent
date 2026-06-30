"""PptxGenJS renderer intermediate schema."""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "color", "fill", "line_color", "header_fill", "header_color",
        "border_color", "background", check_fields=False,
    )
    @classmethod
    def validate_hex_color(cls, value: str) -> str:
        normalized = value.removeprefix("#").upper()
        if len(normalized) != 6 or any(character not in "0123456789ABCDEF" for character in normalized):
            raise ValueError("color must be a 6-digit hexadecimal value")
        return normalized


class PositionedNode(SpecModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class TextNode(PositionedNode):
    type: Literal["text"] = "text"
    text: str
    font_size: float = Field(default=20, gt=0, le=200)
    font_face: str = "Microsoft YaHei"
    color: str = "222222"
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"
    valign: Literal["top", "mid", "bottom"] = "top"
    margin: float = Field(default=0, ge=0)
    bullet: bool = False

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ShapeNode(PositionedNode):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "roundRect", "line", "ellipse"] = "rect"
    fill: str = "FFFFFF"
    line_color: str = "FFFFFF"
    line_width: float = Field(default=0, ge=0)


class ImageNode(PositionedNode):
    type: Literal["image"] = "image"
    path: str
    transparency: int = Field(default=0, ge=0, le=100)

    @field_validator("path")
    @classmethod
    def path_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path must not be blank")
        return value


class RasterLayerNode(ImageNode):
    type: Literal["raster"] = "raster"


class TableNode(PositionedNode):
    type: Literal["table"] = "table"
    rows: list[list[str]]
    font_size: float = Field(default=14, gt=0, le=100)
    color: str = "222222"
    header_fill: str = "1F4E78"
    header_color: str = "FFFFFF"
    border_color: str = "D9E2F3"

    @model_validator(mode="after")
    def validate_rows(self):
        if not self.rows or not self.rows[0]:
            raise ValueError("table rows must not be empty")
        width = len(self.rows[0])
        if any(len(row) != width for row in self.rows):
            raise ValueError("table rows must have equal column counts")
        return self


class ChartSeries(SpecModel):
    name: str
    values: list[float]


class ChartNode(PositionedNode):
    type: Literal["chart"] = "chart"
    chart_type: Literal["bar", "column", "line", "pie", "doughnut"] = "column"
    labels: list[str]
    series: list[ChartSeries]
    show_legend: bool = True
    show_title: bool = False
    title: str | None = None

    @model_validator(mode="after")
    def validate_chart_data(self):
        if not self.labels or not self.series:
            raise ValueError("chart labels and series must not be empty")
        if any(len(item.values) != len(self.labels) for item in self.series):
            raise ValueError("chart series values must match labels")
        return self


SlideNode = Annotated[
    Union[
        TextNode,
        ShapeNode,
        ImageNode,
        RasterLayerNode,
        TableNode,
        ChartNode,
    ],
    Field(discriminator="type"),
]


class SlideSpec(SpecModel):
    id: str
    layout: str | None = None
    background: str = "FFFFFF"
    nodes: list[SlideNode] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("slide id must not be blank")
        return value


class SlideDeckSpec(SpecModel):
    version: Literal["1.0"] = "1.0"
    title: str
    width: float = Field(default=13.333, gt=0, le=100)
    height: float = Field(default=7.5, gt=0, le=100)
    author: str = "AID Work Agent"
    subject: str | None = None
    warnings: list[str] = Field(default_factory=list)
    slides: list[SlideSpec] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("deck title must not be blank")
        return value

    @model_validator(mode="after")
    def validate_node_bounds(self):
        for slide in self.slides:
            for node in slide.nodes:
                if node.x + node.w > self.width + 1e-6:
                    raise ValueError(f"node exceeds slide width on {slide.id}")
                if node.y + node.h > self.height + 1e-6:
                    raise ValueError(f"node exceeds slide height on {slide.id}")
        return self

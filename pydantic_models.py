from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args, get_origin


@dataclass
class FieldInfo:
    default: Any = Ellipsis
    default_factory: Any = None


def Field(default: Any = Ellipsis, *, default_factory: Any = None, **_: Any) -> FieldInfo:
    return FieldInfo(default=default, default_factory=default_factory)


class BaseModel:
    __fields__: dict[str, FieldInfo] = {}

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.__fields__ = {}
        annotations = getattr(cls, "__annotations__", {})
        for name in annotations:
            raw = getattr(cls, name, Ellipsis)
            if isinstance(raw, FieldInfo):
                cls.__fields__[name] = raw
            elif raw is Ellipsis:
                cls.__fields__[name] = FieldInfo(default=Ellipsis)
            else:
                cls.__fields__[name] = FieldInfo(default=raw)

    def __init__(self, **data: Any) -> None:
        annotations = getattr(self.__class__, "__annotations__", {})
        for name, field in self.__class__.__fields__.items():
            if name in data:
                value = self._convert_value(annotations.get(name), data[name])
            elif field.default_factory is not None:
                value = field.default_factory()
            elif field.default is not Ellipsis:
                value = field.default
            else:
                raise TypeError(f"Missing required field '{name}'")
            setattr(self, name, value)

    def _convert_value(self, annotation: Any, value: Any) -> Any:
        if annotation is None:
            return value
        origin = get_origin(annotation)
        args = get_args(annotation)

        if isinstance(value, dict) and isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation(**value)

        if origin is list and args:
            inner = args[0]
            if isinstance(value, list):
                return [self._convert_value(inner, v) for v in value]

        if origin is dict and len(args) == 2:
            k_t, v_t = args
            if isinstance(value, dict):
                return {self._convert_value(k_t, k): self._convert_value(v_t, v) for k, v in value.items()}

        if origin is not None and type(None) in args:
            non_none = [a for a in args if a is not type(None)]
            if value is None or not non_none:
                return value
            return self._convert_value(non_none[0], value)

        return value

    def model_copy(self, update: dict[str, Any] | None = None):
        payload = self.model_dump()
        if update:
            payload.update(update)
        return self.__class__(**payload)

    def model_dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self.__class__.__fields__:
            value = getattr(self, name)
            if isinstance(value, BaseModel):
                out[name] = value.model_dump()
            elif isinstance(value, list):
                out[name] = [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
            elif isinstance(value, dict):
                out[name] = {
                    k: (v.model_dump() if isinstance(v, BaseModel) else v)
                    for k, v in value.items()
                }
            else:
                out[name] = value
        return out

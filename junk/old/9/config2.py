# todo: test cycle detection with ConfigView, across different config inputs
# todo: we can handle option_defaults by assigning a merge or a configview to each option with first dict being a Config of (or can we just pass it directly?) option_defaults 
#       same with user_defaults and users
# i wonder if we could just do child = child | option_defaults   or child |= option_defaults?
# todo: provide a function to return a regular old dict of the Config or ConfigView object for passing to yaml.dump
# may have to do something similar for input_fields too. 
# test if we can use anchors across yaml files
# note that there's currently no way to distinguish between a value being defined as null and the value 
#  not existing.

import os, pathlib
from collections import defaultdict
from collections.abc import Mapping, Iterable
from string import Formatter

import yaml, yaml_include

from definitions import *

yaml.add_constructor("!include", yaml_include.Constructor(base_dir=None), Loader=yaml.SafeLoader)

class Config(defaultdict):
    def __init__(self, source=None, *, _root=None, strict=True):
        super().__init__(lambda: null) 
        self._root = _root or self

        if _root is None:
          self._strict = strict
          self._resolving = set()  
        else:
          self._strict = _root._strict

        self._sealed = False

        if source is None:
            return

        if isinstance(source, Mapping):
            for k, v in source.items():
                self[k] = self._wrap(v)
        else:
            for k, v in vars(source).items():
                self[k] = self._wrap(v)

    def __repr__(self):
        return dict.__repr__(self) # prevent the __init__ function from showing up when printing the dictionary's contents

    def __getattr__(self, name):
        return self._get(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self[name] = self._wrap(value)

    def __delattr__(self, name):
        del self[name]

    def __iter__(self):
        """Iterate over values instead of keys to allow iterating over config objects."""
        for key in super().__iter__():
            yield self[key]

    def keys(self):
        """Return an iterator over the keys."""
        return super().__iter__()

    def values(self):
        """Return an iterator over the values (Config objects)."""
        for key in super().__iter__():
            yield self[key]

    def items(self):
        """Return an iterator over (key, value) pairs."""
        for key in super().__iter__():
            yield (key, self[key])

    # -----------------------------
    # Lazy resolution
    # -----------------------------

    def __getitem__(self, key):
        value = super().__getitem__(key)
        if getattr(self._root, '_resolving', null) is null:
            self._root._resolving = set()
        self._root._resolving.add(key)
        try:
             result = self._resolve(value)
             return null if result is None else result
        finally:
            self._root._resolving.discard(key)

    def _get(self, key, default=null):
        value = super().get(key, default)
        if value is null:
            return value
        if getattr(self._root, '_resolving', null) is null:
            self._root._resolving = set()
        self._root._resolving.add(key)
        try:
            result = self._resolve(value)
            return null if result is None else result
        finally:
            self._root._resolving.discard(key)
    
    def _wrap(self, value):
        if isinstance(value, Config):
            return value

        if isinstance(value, Mapping):
            return Config(value, _root=self._root)

        if hasattr(value, "__dict__") and not callable(value):
            return Config(vars(value), _root=self._root)

        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            return [self._wrap(v) for v in value]

        return value  
      
    def _resolve(self, value):
        if not isinstance(value, str):
            return value

        value = os.path.expandvars(value)

        formatter = _DotFormatter(
            strict=self._strict,
            resolving=self._root._resolving,
        )

        return formatter.format(value, self)    

    def seal(self):
        self._sealed = True
        for v in self.values():
            if isinstance(v, Config):
                v.seal()
        return self

    def merge(self, *sources, deep=True):
        """In-place merge."""
        def do_merge(target, source):
            items = source.items() if isinstance(source, Mapping) else vars(source).items()

            for k, v in items:
                if (
                    deep
                    and k in target
                    and isinstance(target[k], Config)
                    and isinstance(v, Mapping)
                ):
                    do_merge(target[k], v)
                else:
                    target[k] = target._wrap(v)

        for src in sources:
            do_merge(self, src)

        def fix_root(cfg):
            cfg._root = self
            for v in cfg.values():
                if isinstance(v, Config):
                    fix_root(v)

        fix_root(self)
        return self

    def extended(self, *sources, deep=True):
        new = Config(self, strict=self._root._strict)
        new.merge(*sources, deep=deep)
        return new

    # -----------------------------
    # Operators
    # -----------------------------

    def __or__(self, other):  # a = b | c
      return ConfigView(other, self)

    def __ior__(self, other): # a != b
      return self.merge(other)

    def shallow(self, *sources):
        return self.extended(*sources, deep=False)

class _DotFormatter(Formatter):
    
    def __init__(self, *, strict=True, resolving=None, root=None):
        self.strict = strict
        self.resolving = resolving if resolving is not None else set()
        self.root = root if root is not None else {}

    def get_field(self, field_name, args, kwargs):
        if field_name in self.resolving:
            raise ValueError(f"Interpolation cycle detected: {field_name}")

        self.resolving.add(field_name)
        try:
            cur = args[0]
            for part in field_name.split("."):
                if not isinstance(cur, Mapping):
                    raise KeyError(part)
                cur = cur[part]

            return cur, field_name
        finally:
            self.resolving.discard(field_name)

class _MergeDirective:
    __slots__ = ("source", "deep")

    def __init__(self, source, *, deep):
        self.source = source
        self.deep = deep

def shallow(source):
    return _MergeDirective(source, deep=False)

def deep(source):
    return _MergeDirective(source, deep=True)

class ConfigView(Mapping):
    def __init__(self, *layers, write_to=None, _root=None):
        if not layers:
            raise ValueError("ConfigView requires at least one layer")

        self._layers = [
            layer if isinstance(layer, (Config, ConfigView)) else Config(layer)
            for layer in layers
        ]

        # Root for interpolation & strictness
        self._root = _root if _root is not None else self
        self._strict = self._layers[0]._strict

        self._write_to = write_to

    def __getitem__(self, key):
        for layer in self._layers:
            if key in layer:
                value = layer[key]

                # Nested mapping → return another view
                if isinstance(value, Config):
                    nested_layers = []
                    for l in self._layers:
                        if key in l and isinstance(l[key], Config):
                            nested_layers.append(l[key])
                    return ConfigView(*nested_layers, _root=self._root)

                result = self._resolve(value)
                return null if result is None else result

        return null

    def __setitem__(self, key, value):
        if self._write_to is null:
            raise TypeError("ConfigView is read-only")

        # Ensure nested structure exists in write layer
        if isinstance(value, Mapping):
            value = Config(value)

        if getattr(self, "_sealed", False):
            raise TypeError("Config is sealed")

        self._write_to[key] = value

    def __iter__(self):
        """Iterate over values instead of keys to allow iterating over config objects."""
        seen = set()
        for layer in self._layers:
            for k in dict.keys(layer) if isinstance(layer, Config) else layer.keys():
                if k not in seen:
                    seen.add(k)
                    yield self[k]

    def keys(self):
        """Return an iterator over the keys."""
        seen = set()
        for layer in self._layers:
            for k in dict.keys(layer) if isinstance(layer, Config) else layer.keys():
                if k not in seen:
                    seen.add(k)
                    yield k

    def values(self):
        """Return an iterator over the values (Config objects)."""
        seen = set()
        for layer in self._layers:
            for k in dict.keys(layer) if isinstance(layer, Config) else layer.keys():
                if k not in seen:
                    seen.add(k)
                    yield self[k]

    def items(self):
        """Return an iterator over (key, value) pairs."""
        seen = set()
        for layer in self._layers:
            for k in dict.keys(layer) if isinstance(layer, Config) else layer.keys():
                if k not in seen:
                    seen.add(k)
                    yield (k, self[k])

    def __len__(self):
        seen = set()
        for layer in self._layers:
            for k in dict.keys(layer) if isinstance(layer, Config) else layer.keys():
                seen.add(k)
        return len(seen)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            return null

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self[name] = value

    def _resolve(self, value):
        if not isinstance(value, str):
            return value

        value = os.path.expandvars(value)

        formatter = _DotFormatter(
            strict=self._root._strict,
            resolving=self._root._resolving,
        )

        try:
            return formatter.format(value, self._root)
        except KeyError:
            if self._root._strict:
                raise
            return value

    def freeze(self):
        def materialize(obj):
            if isinstance(obj, ConfigView):
                return Config(
                    {k: materialize(obj[k]) for k in obj},
                    strict=self._strict,
                )
            return obj

        return materialize(self)
    
    def source(self, path):
        parts = self._normalize_path(path)
        results = []

        for layer in self._layers:
            cur = layer
            for p in parts:
                if isinstance(cur, Mapping) and p in cur:
                    cur = cur[p]
                else:
                    cur = null
                    break
            results.append((layer, cur))

        return results

    def __or__(self, other):
        return ConfigView(other, *self._layers)

    def __ior__(self, other):
        raise TypeError("ConfigView is immutable")

configs = {}
main_path = None
def get_config(*dicts, path=None, main=False): # because of this, no module that loads the config can be run on its own. the main BBS is meant to be the only one to be run on its own.
  global main_path
  if main and path: 
    main_path = path    
  if not path:
    path = main_path
  if not path in configs:
    configs[path] = Config(yaml.safe_load(open(path, "r").read()))
  return ConfigView(*dicts, configs[path])

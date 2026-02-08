import json
import copy
import traceback
import weakref
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Generator


class DataError(Exception):
	"""Base error for Data-related failures."""
	pass

class ComputationError(DataError):
	"""Raised when a computed/lazy/view callback fails."""
	def __init__(self, key: str, orig_exc: Exception, tb: str):
		super().__init__(f"Computation for '{key}' failed: {orig_exc}")
		self.key = key
		self.orig_exc = orig_exc
		self.traceback = tb

class TransactionError(DataError):
	"""Raised when a transaction fails to rollback cleanly."""
	pass

class PathError(DataError):
	"""Raised for invalid path operations (get/set)."""
	pass

class SerializationError(DataError):
	"""Raised when serialization / to_dict fails."""
	pass


class Method:
	def __init__(self, fn: Callable[..., Any]):
		if not callable(fn):
			raise TypeError("Method expects a callable")
		self.fn = fn
	
	def bind(self, data: "Data", name: str) -> Callable:
		def bound(*args, **kwargs):
			try:
				return self.fn(data, *args, **kwargs)
			except Exception as e:
				tb = traceback.format_exc()
				raise ComputationError(name, e, tb) from e
		return bound

class Computed:
	def __init__(self, fn: Callable[["Data"], Any]):
		if not callable(fn):
			raise TypeError("Computed expects a callable")
		self.fn = fn
	
	def compute(self, data: "Data", key: str = "<computed>") -> Any:
		try:
			return self.fn(data)
		except Exception as e:
			tb = traceback.format_exc()
			print(f"Computed failed for key {key}")
			raise ComputationError(key, e, tb) from e

class Lazy:
	def __init__(self, fn: Callable[["Data"], Any]):
		if not callable(fn):
			raise TypeError("Lazy expects a callable")
		self.fn = fn
		self._cache = weakref.WeakKeyDictionary()
	
	def get(self, data: "Data", key: str = "<lazy>") -> Any:
		if data not in self._cache:
			try:
				self._cache[data] = self.fn(data)
			except Exception as e:
				tb = traceback.format_exc()
				raise ComputationError(key, e, tb) from e
		return self._cache[data]
	
	def invalidate(self, data=None) -> None:
		if data is None:
			self._cache.clear()
		else:
			self._cache.pop(data, None)

class View:
	def __init__(self, source: "Data", mapping: Dict[str, Callable[["Data"], Any]]):
		self._source = source
		self._mapping = mapping
	
	def __getattr__(self, name: str) -> Any:
		if name in self._mapping:
			fn = self._mapping[name]
			if not callable(fn):
				raise ComputationError(name, TypeError("view mapping value is not callable"), traceback.format_stack())
			try:
				return fn(self._source)
			except ComputationError:
				# already wrapped - re-raise
				raise
			except Exception as e:
				tb = traceback.format_exc()
				print(f"View computation failed for {name}")
				raise ComputationError(name, e, tb) from e
		raise AttributeError(name)
	
	def __repr__(self) -> str:
		return f"<View {list(self._mapping)}>"


class AntiFreeze:
	def __init__(self, value: Any):
		self.value = value
	
	def unwrap(self) -> Any:
		return self.value
	
	def __repr__(self) -> str:
		return f"AntiFreeze({self.value!r})"

class FrozenDict(dict):
	def __readonly(self, *a, **k) -> None:
		raise TypeError("FrozenDict is immutable")
	
	__setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __readonly


class Data:
	__slots__ = ('__dict__', '__weakref__')
	
	def __init__(self, **kwargs: Any):
		super().__setattr__("_Data__frozen", False)
		super().__setattr__("_Data__watchers", [])
		super().__setattr__("_Data__lazy_fields", {})
		super().__setattr__("_Data__transaction_stack", [])
		super().__setattr__("_Data__anti_freeze_fields", set())
		super().__setattr__("_Data__methods", {})
		
		# assign with validation
		for k, v in kwargs.items():
			if not isinstance(k, str) or not k.isidentifier():
				raise DataError(f"Invalid key: {k!r} (must be a valid identifier)")
			
			# AntiFreeze marker (unwrap immediately)
			if isinstance(v, AntiFreeze):
				self.__anti_freeze_fields.add(k)
				v = v.unwrap()
			
			super().__setattr__(k, v)
		
		# compute Computed, register Lazy and Method
		for k, v in list(self.__dict__.items()):
			if isinstance(v, Method):
				self.__methods[k] = v
				super().__setattr__(k, None)
			
			elif isinstance(v, Computed):
				val = v.compute(self, key=k)
				
				# allow Computed to return AntiFreeze
				if isinstance(val, AntiFreeze):
					self.__anti_freeze_fields.add(k)
					val = val.unwrap()
				
				super().__setattr__(k, val)
			
			elif isinstance(v, Lazy):
				self.__lazy_fields[k] = v
				super().__setattr__(k, None)
	
	# ---------------- core ----------------
	
	def freeze(self) -> "Data":
		def _freeze(v):
			if isinstance(v, Data):
				v.freeze()
				return v
			if isinstance(v, dict):
				return FrozenDict({k: _freeze(i) for k, i in v.items()})
			if isinstance(v, list):
				return tuple(_freeze(i) for i in v)
			if isinstance(v, set):
				return frozenset(_freeze(i) for i in v)
			return v
		
		if not self.__frozen:
			for k, v in list(self.__dict__.items()):
				if k.startswith("_Data__"):
					continue
				if k in self.__anti_freeze_fields:
					continue
				
				try:
					super().__setattr__(k, _freeze(v))
				except Exception as e:
					tb = traceback.format_exc()
					raise DataError(
						f"Error freezing key '{k}': {e}\n{tb}"
					) from e
			
			super().__setattr__("_Data__hash", None)
			super().__setattr__("_Data__frozen", True)
		
		return self
	
	# ---------------- lazy ----------------
	
	def __getattribute__(self, name: str) -> Any:
		d = object.__getattribute__(self, "__dict__")
		
		methods = d.get("_Data__methods", {})
		if name in methods:
			return methods[name].bind(self, name)
		
		lazy = d.get("_Data__lazy_fields", {})
		if name in lazy:
			try:
				return lazy[name].get(self, key=name)
			except ComputationError:
				raise
			except Exception as e:
				tb = traceback.format_exc()
				raise ComputationError(name, e, tb) from e
		
		return object.__getattribute__(self, name)
	
	def _invalidate_lazy(self) -> None:
		for lazy in self.__lazy_fields.values():
			try:
				lazy.invalidate()
			except Exception:
				print("Lazy.invalidate failed")
	
	# ---------------- observers ----------------
	
	def watch(self, fn: Callable[[str, Any, Any], None]) -> None:
		if not callable(fn):
			raise TypeError("watch() expects a callable")
		self.__watchers.append(fn)

	def _notify(self, key: str, old: Any, new: Any) -> None:
		for w in list(self.__watchers):
			try:
				w(key, old, new)
			except Exception:
				# watcher must not break core logic; log and continue
				print(f"Watcher raised for key {key}")
	
	# ---------------- setattr ----------------
	
	def __setattr__(self, key: str, value: Any) -> None:
		if self.__frozen and key not in self.__anti_freeze_fields:
			raise AttributeError(f"Data is frozen (cannot modify '{key}')")
		
		if not isinstance(key, str) or not key.isidentifier():
			raise DataError(f"Invalid attribute name: {key!r}")
		
		if not self.__frozen:
			super().__setattr__("_Data__hash", None)
		
		old = self.__dict__.get(key, None)
		super().__setattr__(key, value)
		
		try:
			self._invalidate_lazy()
		except Exception:
			print(f"Failed to invalidate lazy fields after setting {key}")
		
		try:
			self._notify(key, old, value)
		except Exception:
			print(f"Failed to notify watchers after setting {key}")
	
	# ---------------- views ----------------
	
	def view(self, mapping: Dict[str, Callable[["Data"], Any]]) -> View:
		if not isinstance(mapping, dict):
			raise TypeError("view() mapping must be a dict")
		return View(self, mapping)
	
	# ---------------- path access ----------------
	
	def get(self, path: str, default: Any = None) -> Any:
		if not isinstance(path, str) or path == "":
			raise PathError("path must be a non-empty string")
		cur: Any = self
		for part in path.split("."):
			if isinstance(cur, Data):
				# use getattr with default so missing attr returns default
				cur = getattr(cur, part, default)
			elif isinstance(cur, dict):
				cur = cur.get(part, default)
			else:
				# cannot traverse further
				return default
		return cur
	
	def set(self, path: str, value: Any) -> None:
		if not isinstance(path, str) or path == "":
			raise PathError("path must be a non-empty string")
		parts = path.split(".")
		cur: Any = self
		for p in parts[:-1]:
			n = getattr(cur, p, None) if isinstance(cur, Data) else (cur.get(p) if isinstance(cur, dict) else None)
			if n is None:
				n = Data()
				if isinstance(cur, Data):
					setattr(cur, p, n)
				elif isinstance(cur, dict):
					cur[p] = n
				else:
					raise PathError(f"Cannot create path at part {p!r} (parent is {type(cur).__name__})")
			cur = n
		# final set
		if isinstance(cur, Data):
			setattr(cur, parts[-1], value)
		elif isinstance(cur, dict):
			cur[parts[-1]] = value
		else:
			raise PathError(f"Cannot set path {path!r} (parent is {type(cur).__name__})")
	
	# ---------------- diff / patch ----------------
	
	def diff(self, other: "Data") -> Dict[str, Tuple[Any, Any]]:
		if not isinstance(other, Data):
			raise TypeError("diff() expects another Data instance")
		out: Dict[str, Tuple[Any, Any]] = {}
		keys = set(self.__dict__) | set(other.__dict__)
		for k in keys:
			if k.startswith("_Data__"):
				continue
			a = self.__dict__.get(k)
			b = other.__dict__.get(k)
			if a != b:
				out[k] = (b, a)
		return out
	
	def apply(self, patch: Dict[str, Tuple[Any, Any]]) -> None:
		if not isinstance(patch, dict):
			raise TypeError("apply() expects a dict patch")
		for k, pair in patch.items():
			try:
				_, new = pair
			except Exception:
				raise DataError(f"Invalid patch entry for key {k!r}: {pair!r}")
			setattr(self, k, new)
	
	# ---------------- transactions ----------------
	
	@contextmanager
	def transaction(self) -> Generator[None, None, None]:
		try:
			snapshot = {
				k: copy.deepcopy(v)
				for k, v in self.__dict__.items()
				if not k.startswith("_Data__")
			}
		except Exception as e:
			tb = traceback.format_exc()
			print("Failed to snapshot state for transaction")
			raise TransactionError(f"Failed to snapshot state: {e}\n{tb}") from e
		
		self.__transaction_stack.append(snapshot)
		try:
			yield
			# commit: pop snapshot
			self.__transaction_stack.pop()
		except Exception as e:
			# rollback
			try:
				state = self.__transaction_stack.pop()
				
				for k in list(self.__dict__.keys()):
					if not k.startswith("_Data__"):
						del self.__dict__[k]
				for k, v in state.items():
					if not k.startswith("_Data__"):
						self.__dict__[k] = v
				
				print(f"Transaction failed and was rolled back due to: {e}")
			except Exception as inner:
				tb = traceback.format_exc()
				print("Rollback failed")
				raise TransactionError(f"Rollback failed: {inner}\n{tb}") from inner
			# re-raise original error for caller to handle
			raise
	
	# ---------------- snapshot ----------------
	
	def snapshot(self) -> "Data":
		try:
			return copy.deepcopy(self)
		except Exception as e:
			tb = traceback.format_exc()
			print("Snapshot failed")
			raise DataError(f"Snapshot failed: {e}\n{tb}") from e
	
	# ---------------- serialization ----------------
	
	def to_dict(self, _memo: Optional[Set[int]] = None, *, for_hash: bool = False) -> Dict[str, Any]:
		try:
			if _memo is None:
				_memo = set()
			if id(self) in _memo:
				return {"$circular": True}
			_memo.add(id(self))
			
			out: Dict[str, Any] = {}
			for k, v in self.__dict__.items():
				if k.startswith("_Data__"):
					continue
				if for_hash:
					if k in self.__anti_freeze_fields:
						continue
					if k in self.__lazy_fields:
						continue
				
				if isinstance(v, Data):
					out[k] = v.to_dict(_memo, for_hash=for_hash)
				elif isinstance(v, dict):
					out[k] = {
						kk: vv.to_dict(_memo, for_hash=for_hash)
						if isinstance(vv, Data) else vv
						for kk, vv in v.items()
					}
				elif isinstance(v, (list, tuple, set)):
					out[k] = [
						i.to_dict(_memo, for_hash=for_hash)
						if isinstance(i, Data) else i
						for i in v
					]
				else:
					out[k] = v
			
			return out
		except Exception as e:
			tb = traceback.format_exc()
			raise SerializationError(f"to_dict failed: {e}\n{tb}") from e
	
	# ---------------- dunder ----------------
	
	def __repr__(self) -> str:
		try:
			return f"Data({self.to_dict()})"
		except SerializationError:
			return f"<Data (unserializable) at {hex(id(self))}>"
	
	def __eq__(self, other: object) -> bool:
		if self is other:
			return True
		
		if not isinstance(other, Data):
			return NotImplemented
		
		if not self.__frozen or not other.__frozen:
			return False
		
		try:
			return self.to_dict(for_hash=True) == other.to_dict(for_hash=True)
		except Exception:
			return False
	
	def __hash__(self) -> int:
		if not self.__frozen:
			raise TypeError("Unfrozen Data is unhashable")
		
		h = getattr(self, "_Data__hash", None)
		if h is not None:
			return h
		
		try:
			h = hash(json.dumps(
				self.to_dict(for_hash=True),
				sort_keys=True
			))
			super().__setattr__("_Data__hash", h)
			return h
		except Exception as e:
			tb = traceback.format_exc()
			raise SerializationError(f"Hashing failed: {e}\n{tb}") from e

__all__ = (
	'Data',
	
	'AntiFreeze',
	
	'Method',
	'Computed',
	'Lazy',
	'View'
)
__version__ = "2.1.0"
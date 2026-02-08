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
	"""
	Raised when a computed, lazy, or view callback fails.
	
	Attributes:
		key (str): The name of the field that failed to compute.
		orig_exc (Exception): The original exception raised.
		traceback (str): The formatted traceback string.
	"""
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
	"""Raised when serialization or to_dict conversion fails."""
	pass


class Method:
	"""
	Wraps a function to be bound to a Data instance at runtime.
	"""
	def __init__(self, fn: Callable[..., Any]):
		"""
		Initializes the Method wrapper.
		
		Args:
			fn: The function to be wrapped.
		
		Raises:
			TypeError: If fn is not callable.
		"""
		if not callable(fn):
			raise TypeError("Method expects a callable")
		self.fn = fn
	
	def bind(self, data: "Data", name: str) -> Callable:
		"""
		Binds the function to a specific Data instance.
		
		Args:
			data: The Data instance to bind to.
			name: The name assigned to this method in the Data instance.
		
		Returns:
			A wrapper function that injects the Data instance as the first argument.
		"""
		def bound(*args, **kwargs):
			try:
				return self.fn(data, *args, **kwargs)
			except Exception as e:
				tb = traceback.format_exc()
				raise ComputationError(name, e, tb) from e
		return bound

class Computed:
	"""
	Represents a value that is calculated once during Data initialization.
	"""
	def __init__(self, fn: Callable[["Data"], Any]):
		"""
		Initializes the Computed field.
		
		Args:
			fn: A callable that accepts a Data instance and returns a value.
		
		Raises:
			TypeError: If fn is not callable.
		"""
		if not callable(fn):
			raise TypeError("Computed expects a callable")
		self.fn = fn
	
	def compute(self, data: "Data", key: str = "<computed>") -> Any:
		"""
		Executes the computation.
		
		Args:
			data: The Data instance to provide to the function.
			key: The name of the field for error reporting.
		
		Returns:
			The result of the computation.
		"""
		try:
			return self.fn(data)
		except Exception as e:
			tb = traceback.format_exc()
			print(f"Computed failed for key {key}")
			raise ComputationError(key, e, tb) from e

class Lazy:
	"""
	Represents a value that is computed only when first accessed and then cached.
	"""
	def __init__(self, fn: Callable[["Data"], Any]):
		"""
		Initializes the Lazy field.
		
		Args:
			fn: A callable that accepts a Data instance and returns a value.
		
		Raises:
			TypeError: If fn is not callable.
		"""
		if not callable(fn):
			raise TypeError("Lazy expects a callable")
		self.fn = fn
		self._cache = weakref.WeakKeyDictionary()
	
	def get(self, data: "Data", key: str = "<lazy>") -> Any:
		"""
		Retrieves the cached value or computes it if missing.
		
		Args:
			data: The Data instance acting as the cache key.
			key: The name of the field for error reporting.
		
		Returns:
			The cached or newly computed value.
		"""
		if data not in self._cache:
			try:
				self._cache[data] = self.fn(data)
			except Exception as e:
				tb = traceback.format_exc()
				raise ComputationError(key, e, tb) from e
		return self._cache[data]
	
	def invalidate(self, data=None) -> None:
		"""
		Clears the cache for a specific Data instance or all instances.
		
		Args:
			data: The specific Data instance to invalidate. If None, clears all.
		"""
		if data is None:
			self._cache.clear()
		else:
			self._cache.pop(data, None)

class View:
	"""
	A read-only dynamic view into a Data instance.
	"""
	def __init__(self, source: "Data", mapping: Dict[str, Callable[["Data"], Any]]):
		"""
		Initializes the View.
		
		Args:
			source: The Data instance to observe.
			mapping: A dictionary mapping attribute names to access functions.
		"""
		self._source = source
		self._mapping = mapping
	
	def __getattr__(self, name: str) -> Any:
		"""
		Resolves a view attribute using the provided mapping.
		
		Args:
			name: The attribute name to resolve.
		
		Returns:
			The result of the mapped function.
		
		Raises:
			AttributeError: If name is not in mapping.
			ComputationError: If the mapping function fails.
		"""
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
	"""
	A wrapper used to mark specific fields to remain mutable even if the Data object is frozen.
	"""
	def __init__(self, value: Any):
		self.value = value
	
	def unwrap(self) -> Any:
		"""Returns the internal value."""
		return self.value
	
	def __repr__(self) -> str:
		return f"AntiFreeze({self.value!r})"

class FrozenDict(dict):
	"""
	A dictionary subclass that prevents modification.
	"""
	def __readonly(self, *a, **k) -> None:
		raise TypeError("FrozenDict is immutable")
	
	__setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __readonly


class Data:
	"""
	A robust data container supporting reactivity, freezing, transactions, and path-based access.
	
	Attributes:
		__frozen (bool): Whether the object is immutable.
		__watchers (List[Callable]): Functions called on attribute changes.
	"""
	__slots__ = ('__dict__', '__weakref__')
	
	def __init__(self, **kwargs: Any):
		"""
		Initializes the Data object with provided keyword arguments.
		
		Processes Method, Computed, and Lazy types. AntiFreeze values are unwrapped
		and registered for immunity from freezing.
		
		Args:
			**kwargs: Initial key-value pairs. Keys must be valid identifiers.
		
		Raises:
			DataError: If a key is not a valid Python identifier.
		"""
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
	
	# 1. Access & Mutation
	
	def __getattribute__(self, name: str) -> Any:
		"""
		Custom attribute access to resolve Methods and Lazy fields.
		"""
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
	
	def __setattr__(self, key: str, value: Any) -> None:
		"""
		Sets an attribute, triggers invalidation, and notifies watchers.
		
		Args:
			key: The attribute name.
			value: The value to set.
		
		Raises:
			AttributeError: If the Data instance is frozen.
			DataError: If the key is not a valid identifier.
		"""
		if self.__frozen and key not in self.__anti_freeze_fields:
			raise AttributeError(f"Data is frozen (cannot modify '{key}')")
		
		if not isinstance(key, str) or not key.isidentifier():
			raise DataError(f"Invalid attribute name: {key!r}")
		
		if not self.__frozen:
			super().__setattr__("_Data__hash", None)
		
		old = self.__dict__.get(key, None)
		super().__setattr__(key, value)
		
		try:
			for lazy in self.__lazy_fields.values():
				lazy.invalidate()
		except Exception:
			print(f"Failed to invalidate lazy fields after setting {key}")
		
		try:
			self._notify(key, old, value)
		except Exception:
			print(f"Failed to notify watchers after setting {key}")
	
	def get(self, path: str, default: Any = None) -> Any:
		"""
		Retrieves a nested value using dot-notation (e.g., 'user.profile.name').
		
		Args:
			path: Dot-separated string of keys/attributes.
			default: Value to return if path is not found.
		
		Returns:
			The value at the path or the default.
		"""
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
		"""
		Sets a nested value using dot-notation, creating intermediate Data objects if needed.
		
		Args:
			path: Dot-separated string of keys/attributes.
			value: The value to set at the end of the path.
		
		Raises:
			PathError: If the path is invalid or cannot be traversed.
		"""
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
	
	# 2. State Control
	
	def freeze(self) -> "Data":
		"""
		Recursively converts the Data object and its contents into immutable types.
		
		Dictionaries become FrozenDicts, lists become tuples, and sets become frozensets.
		Fields marked with AntiFreeze remain mutable.
		
		Returns:
			The current Data instance (frozen).
		
		Raises:
			DataError: If freezing a specific key fails.
		"""
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
	
	@contextmanager
	def transaction(self) -> Generator[None, None, None]:
		"""
		A context manager that snapshots state and rolls back if an exception occurs.
		
		Raises:
			TransactionError: If the snapshot or rollback process fails.
		"""
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
	
	# 3. Observation & Sync
	
	def watch(self, fn: Callable[[str, Any, Any], None]) -> None:
		"""
		Registers a callback to be triggered when any attribute is changed.
		
		Args:
			fn: A callable taking (key, old_value, new_value).
		"""
		if not callable(fn):
			raise TypeError("watch() expects a callable")
		self.__watchers.append(fn)
	
	def _notify(self, key: str, old: Any, new: Any) -> None:
		"""Internal helper to notify all watchers of a change."""
		for w in list(self.__watchers):
			try:
				w(key, old, new)
			except Exception:
				# watcher must not break core logic; log and continue
				print(f"Watcher raised for key {key}")
	
	def diff(self, other: "Data") -> Dict[str, Tuple[Any, Any]]:
		"""
		Compares this Data instance with another and returns the differences.
		
		Args:
			other: Another Data instance to compare against.
		
		Returns:
			A dict mapping keys to tuples of (new_value, old_value).
		"""
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
		"""
		Applies a patch (like one generated by diff) to this instance.
		
		Args:
			patch: A dict mapping keys to tuples where the second element is the new value.
		"""
		if not isinstance(patch, dict):
			raise TypeError("apply() expects a dict patch")
		for k, pair in patch.items():
			try:
				_, new = pair
			except Exception:
				raise DataError(f"Invalid patch entry for key {k!r}: {pair!r}")
			setattr(self, k, new)
	
	# 4. Utilities & Dunders
	
	def to_dict(self, _memo: Optional[Set[int]] = None, *, for_hash: bool = False) -> Dict[str, Any]:
		"""
		Recursively converts the Data object into a standard Python dictionary.
		
		Args:
			_memo: Internal set for circular reference detection.
			for_hash: If True, excludes AntiFreeze and Lazy fields to ensure stable hashing.
		
		Returns:
			A dictionary representation of the Data instance.
		"""
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
	
	def snapshot(self) -> "Data":
		"""
		Creates a deep copy of the current Data instance.
		
		Returns:
			A new Data instance with identical data.
		"""
		try:
			return copy.deepcopy(self)
		except Exception as e:
			tb = traceback.format_exc()
			print("Snapshot failed")
			raise DataError(f"Snapshot failed: {e}\n{tb}") from e
	
	def view(self, mapping: Dict[str, Callable[["Data"], Any]]) -> View:
		"""
		Creates a dynamic View into this Data instance.
		
		Args:
			mapping: Dict mapping view-attribute names to computation functions.
		
		Returns:
			A new View instance.
		"""
		if not isinstance(mapping, dict):
			raise TypeError("view() mapping must be a dict")
		return View(self, mapping)
	
	def __eq__(self, other: object) -> bool:
		"""Checks equality. Only valid if both objects are frozen."""
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
		"""Computes a hash based on serialized data. Requires the object to be frozen."""
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
	
	def __repr__(self) -> str:
		try:
			return f"Data({self.to_dict()})"
		except SerializationError:
			return f"<Data (unserializable) at {hex(id(self))}>"

__all__ = ('Data', 'FrozenDict', 'AntiFreeze', 'Method', 'Computed', 'Lazy', 'View')
__version__ = "2.1.0"
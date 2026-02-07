# Data Container

A **reactive, transactional, diffable, lazy, serializable state container** for Python.

This project provides a single core class (`Data`) that can act as:

* A dynamic attribute-based data container
* A reactive state engine with watchers
* A lazy / computed value system
* A transactional state store (with rollback)
* A diff / patch engine
* A snapshot & freezeable value object
* A JSON-serializable structure with circular reference safety

This is **not** a dataclass replacement, ORM, or schema-first validator. It is designed for **runtime state**, **configuration**, and **application logic glue**.

---

## Table of Contents

* [Why this exists](#why-this-exists)
* [Installation](#installation)
* [Quick Example](#quick-example)
* [Core Concepts](#core-concepts)

  * [Basic Data Usage](#basic-data-usage)
  * [Computed Fields](#computed-fields)
  * [Lazy Fields](#lazy-fields)
  * [Views](#views)
  * [Watchers (Change Tracking)](#watchers-change-tracking)
  * [Path-Based Access](#path-based-access)
  * [Diff & Patch](#diff--patch)
  * [Transactions](#transactions)
  * [Snapshots](#snapshots)
  * [Freezing & Hashing](#freezing--hashing)
  * [Serialization](#serialization)
* [Error Handling Model](#error-handling-model)
* [Design Philosophy](#design-philosophy)
* [Weaknesses & Tradeoffs](#weaknesses--tradeoffs)
* [When You SHOULD Use This](#when-you-should-use-this)
* [When You SHOULD NOT Use This](#when-you-should-not-use-this)
* [FAQ](#faq)

---

## Why this exists

Python has excellent tools for **static data** (`dataclasses`, `attrs`, `pydantic`).

But many applications need **dynamic, evolving state**:

* Bots
* Long-running services
* Configuration systems
* UI / game state
* Hot-reloadable settings
* Derived runtime values

This project focuses on **runtime behavior**, not schemas.

---

## Installation

Copy `datacontainer.py` into your project, or vendor it as a module.

There are **no third-party dependencies**.

---

## Quick Example

```python
from datacontainer import Data, Computed, Lazy

state = Data(
	x=10,
	y=Computed(lambda s: s.x + 5),
	z=Lazy(lambda s: s.x * 2)
)

print(state.y)  # 15
print(state.z)  # 20

state.x = 7
print(state.z)  # 14 (lazy invalidated automatically)
```

---

## Core Concepts

### Basic Data Usage

```python
d = Data(a=1, b=2)

d.a = 5
print(d["a"])  # 5
```

* Attributes and dict-style access both work
* Only valid Python identifiers are allowed as keys

---

### Computed Fields

Computed fields are evaluated **once at initialization**.

```python
d = Data(
	x=10,
	y=Computed(lambda s: s.x * 3)
)

print(d.y)  # 30
```

If computation fails, a `ComputationError` is raised with full traceback context.

---

### Lazy Fields

Lazy fields are evaluated **on first access** and cached.

```python
d = Data(
	x=5,
	y=Lazy(lambda s: expensive_operation(s.x))
)

print(d.y)  # computed now
print(d.y)  # cached
```

Any mutation of the Data object automatically invalidates all lazy caches.

---

### Views

Views are **live projections** of a Data object.

```python
user = Data(first="Ada", last="Lovelace")

view = user.view({
	"full": lambda u: f"{u.first} {u.last}",
	"initials": lambda u: u.first[0] + u.last[0]
})

print(view.full)
```

Views:

* Do not store data
* Always reflect the current state
* Are read-only

---

### Watchers (Change Tracking)

Watchers observe mutations.

```python
def on_change(key, old, new):
	print(key, old, "→", new)

state.watch(on_change)
state.x = 42
```

Watcher errors are logged but **do not interrupt state mutation**.

---

### Path-Based Access

```python
d = Data()
d.set("a.b.c", 10)

print(d.get("a.b.c"))  # 10
```

This is useful for nested configuration and dynamic structures.

---

### Diff & Patch

```python
a = Data(x=1, y=2)
b = Data(x=1, y=3, z=4)

patch = b.diff(a)
# { 'y': (2, 3), 'z': (None, 4) }

a.apply(patch)
```

This enables syncing, undo/redo, and replication.

---

### Transactions

Transactions provide **atomic mutations**.

```python
try:
	with d.transaction():
		d.x = 100
		raise RuntimeError()
except RuntimeError:
	pass

print(d.x)  # original value restored
```

If rollback fails, a `TransactionError` is raised.

---

### Snapshots

```python
snap = d.snapshot()
d.x = 5
print(snap.x)  # unchanged
```

Snapshots are deep copies and completely isolated.

---

### Freezing & Hashing

```python
d.freeze()
hash(d)
```

* Frozen Data is immutable
* Frozen Data is hashable
* Freezing recursively freezes nested structures (dict → FrozenDict, list → tuple, etc.)

**AntiFreeze (Selective Mutability)**

In some cases, full immutability is undesirable.

Examples:

* caches
* runtime handles
* file descriptors
* live connections
* internal counters

For these cases, AntiFreeze allows explicit opt-out from freezing.

```python
from datacontainer import Data, AntiFreeze

d = Data(
	config=Data(mode="prod"),
	cache=AntiFreeze({})
)

d.freeze()

d.cache["x"] = 42        # allowed
d.config.mode = "dev"    # raises AttributeError
```

Key properties:

* Only fields explicitly wrapped in AntiFreeze remain mutable
* All other fields remain deeply frozen
* AntiFreeze must be declared at initialization or computation time
* AntiFreeze is removed after unwrap (it does not exist at runtime)
* This preserves immutability guarantees without sacrificing practicality.

---

### Serialization

```python
print(d.to_dict())
```

* Circular references are handled safely
* Non-serializable errors raise `SerializationError`

---

## Error Handling Model

This library uses **explicit, structured errors**:

* `DataError` – base class
* `ComputationError` – computed / lazy / view failures
* `TransactionError` – rollback failures
* `PathError` – invalid path access
* `SerializationError` – serialization or hashing failures

All errors include contextual messages and captured tracebacks.

---

## Design Philosophy

* Explicit over magical
* Runtime behavior over static schemas
* Safety over silent failure
* Debuggability over raw performance
* Single-file vendorable code

This is intentionally **not** a framework.

---

## Weaknesses & Tradeoffs

This tool is powerful, but it has limitations.

### Performance

* Not optimized for millions of mutations
* Deepcopy-based transactions are expensive

### Thread Safety

* Not thread-safe
* Designed for single-threaded async/event-loop usage

### Memory Usage

* Snapshots and transactions duplicate state
* Lazy cache may retain references

### Schema Validation

* No static typing or validation enforcement
* Invalid data can exist until runtime

---

## When You SHOULD Use This

* Bot state management
* Configuration engines
* UI / game state
* Hot-reloadable settings
* Prototyping complex state behavior
* Glue code between systems

---

## When You SHOULD NOT Use This

* Database models
* Large-scale numerical data
* High-throughput concurrent systems
* Strict schema / validation-heavy domains
* Public API models

Use `dataclasses`, `attrs`, or `pydantic` instead.

---

## FAQ

**Is this a replacement for Pydantic?**
No. This solves a different problem.

**Is it production-ready?**
Yes, if you understand the tradeoffs.

**Why not use descriptors / metaclasses?**
Simplicity, debuggability, and vendoring.

---

## License

MIT
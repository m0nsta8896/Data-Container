# Data Container

A reactive, transactional, and serializable state container for Python.
DataContainer is a single-file, zero-dependency library designed for scenarios where you need more than a dict but less than a database. It bridges the gap between static data structures (like dataclasses) and dynamic runtime state.
It provides a dictionary-like object that supports computed values, lazy loading, change observers, atomic transactions, and deep immutability.

---

## Quick Start

Copy `datacontainer.py` into your project. That's it.

```python
from datacontainer import Data, Computed, Lazy

# Define state with logic attached
config = Data(
    host="localhost",
    port=8080,
    # Computed: Updates automatically when dependencies change
    url=Computed(lambda s: f"http://{s.host}:{s.port}"),
    # Lazy: Only runs once, when accessed
    db_connection=Lazy(lambda s: connect_db(s.host))
)

print(config.url)  # http://localhost:8080

config.port = 9000
print(config.url)  # http://localhost:9000 (Auto-updated)
```

---

## Table of Contents
 * [Core Features](#core-features)
 * [Reactivity & Logic](#reactivity--logic)
   * [Computed Fields](#computed-fields)
   * [Lazy Fields](#lazy-fields)
   * [Attached Methods](#attached-methods)
   * [Watchers](#watchers)
 * [State Management](#state-management)
   * [Path Access](#path-access)
   * [Transactions & Rollbacks](#transactions--rollbacks)
   * [Diffing & Patching](#diffing--patching)
 * [Immutability & Safety](#immutability--safety)
   * [Freezing](#freezing)
   * [AntiFreeze (Selective Mutability)](#antifreeze-selective-mutability)
 * [Serialization](#serialization)
 * [Design Philosophy](#design-philosophy)
 * [Weaknesses & Tradeoffs](#weaknesses--tradeoffs)
   * [Performance](#performance)
   * [Thread Safety](#thread-safety)
   * [Memory Usage](#memory-usage)
   * [Schema Validation](#schema-validation)
 * [When You SHOULD Use This](#when-you-should-use-this)
 * [When You SHOULD NOT Use This](#when-you-should-not-use-this)
 * [FAQ](#faq)

---

## Core Features

Feature       | Description                                                       |
:------------ | :---------------------------------------------------------------- |
Dynamic       | Add or remove fields at runtime like a dictionary.                |
Reactive      | Fields that recalculate based on other fields.                    |
Lazy          | Expensive calculations run only when needed and cache the result. |
Transactional | Make changes in a with block; if it fails, state rolls back.      |
Observable    | Listen for changes on specific keys.                              |
Diffable      | Compare two state objects and generate a patch.                   |
Safe          | Handles circular references in serialization automatically.       |

---

## Reactivity & Logic

### Computed Fields

Computed fields are calculated immediately upon initialization. While they don't dynamically update stored values, they are useful for initial derivation.
(Note: For live updates, use Views or simply access the computed property again if it depends on mutable state).

```python
cart = Data(
    price=100,
    tax_rate=0.2,
    total=Computed(lambda s: s.price * (1 + s.tax_rate))
)

print(cart.total) # 120.0
```

### Lazy Fields

Lazy fields are perfect for expensive operations (loading files, connecting to APIs). The result is cached indefinitely until the Data object is modified.
Automatic Cache Invalidation: If any attribute on the Data object changes, the lazy cache is cleared to ensure consistency.

```python
app = Data(
    theme="dark",
    # This won't run until you ask for it
    assets=Lazy(lambda s: load_heavy_assets(s.theme))
)

x = app.assets # Loads assets...
y = app.assets # Instant (cached)

app.theme = "light" # Invalidates cache
z = app.assets # Reloads assets for light theme
```

### Attached Methods

You can bind functions to the `Data` object, effectively giving it "methods" without defining a class.

```python
from datacontainer import Data, Method

def greet_user(data, time_of_day):
    return f"Good {time_of_day}, {data.name}!"

user = Data(
    name="Alice",
    greet=Method(greet_user)
)

print(user.greet("morning")) # "Good morning, Alice!"
```

### Watchers (Observers)

Trigger side effects when data changes.

```python
def log_change(key, old_val, new_val):
    print(f"[AUDIT] {key} changed from {old_val} to {new_val}")

settings = Data(volume=50)
settings.watch(log_change)

settings.volume = 75 
# Output: [AUDIT] volume changed from 50 to 75
```

---

## State Management

### Path Access

Access nested data deeply without worrying about AttributeError or KeyError.

```python
config = Data(
    server=Data(
        logging=Data(level="INFO")
    )
)

# Dot notation for deeply nested sets
config.set("server.logging.level", "DEBUG")

# Safe gets
level = config.get("server.logging.level") 
missing = config.get("server.database.host", "127.0.0.1") # Default value
```

### Transactions & Rollbacks

Perform atomic updates. If an error occurs within the block, the state reverts to exactly how it was before the block started.

```python
state = Data(credits=100, items=[])

try:
    with state.transaction():
        state.credits -= 50
        state.items.append("Sword")
        
        # Simulate a crash
        raise RuntimeError("Database disconnected!")
except RuntimeError:
    print("Transaction failed, rolling back...")

print(state.credits) # 100 (Safe!)
print(state.items)   # []
```

### Diffing & Patching

Useful for networking (sending only changes) or undo/redo systems.

```python
state_v1 = Data(x=10, y=20)
state_v2 = Data(x=10, y=99)

# Calculate difference
diff = state_v2.diff(state_v1) 
# Result: {'y': (20, 99)}  (Key: (Old, New))

# Apply patch to bring v1 up to date
state_v1.apply(diff)
assert state_v1.y == 99
```

---

## Immutability & Safety

### Freezing

Turn your Data object into a read-only, hashable structure. This is recursive: lists become tuples, sets become frozensets, and dicts become FrozenDicts.

```python
config = Data(host="localhost", ports=[80, 443])
config.freeze()

# config.host = "1.1.1.1"    # Raises AttributeError
# config.ports.append(8080)  # Raises TypeError (it's now a tuple)

# Now it can be used as a dictionary key
cache = {config: "Server Status OK"}
```

### AntiFreeze (Selective Mutability)

Sometimes you need an object to be hashable/frozen, but still keep a specific field mutable (like a cache, a counter, or a network socket).

```python
from datacontainer import Data, AntiFreeze

server = Data(
    id="srv-01",
    # This field remains mutable even after freezing
    metrics=AntiFreeze({"uptime": 0})
)

server.freeze()

# Identity is stable
print(hash(server)) 

# You can still modify the AntiFreeze field
server.metrics["uptime"] = 100 
```

---

## Serialization

Convert to a dictionary safely. DataContainer automatically detects circular references to prevent infinite recursion errors.

```python
parent = Data(name="Parent")
child = Data(name="Child", parent=parent)
parent.child = child

# Standard JSON dump would crash here
data_dict = parent.to_dict()

# Result: 
# {
#   "name": "Parent", 
#   "child": {
#     "name": "Child", 
#     "parent": {"$circular": True}
#   }
# }
```

---

## Design Philosophy
 * Runtime over Schema: No type hints required. Data evolves as the program runs.
 * Explicit Errors: Custom exception types (`ComputationError`, `TransactionError`) make debugging easier.
 * Vendorable: It is a single file. You should feel comfortable copying it directly into your project to avoid dependency hell.

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
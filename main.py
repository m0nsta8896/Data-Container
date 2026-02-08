import traceback
from datacontainer import (
	Data,
	Computed,
	Lazy,
	Method,
	View,
	AntiFreeze,
	ComputationError,
	DataError
)

print("\n=== BASIC CONSTRUCTION ===")

d = Data(
	a=2,
	b=3,
	sum=Computed(lambda self: self.a + self.b),
	product=Lazy(lambda self: self.a * self.b),
	increment=Method(lambda self, x: self.a + x),
	fail=Method(lambda self: 1 / 0)
)

print("sum:", d.sum)					# 5
print("product:", d.product)			# 6
print("increment(5):", d.increment(5))	# 7

print("\n=== LAZY CACHE & INVALIDATION ===")
print(d.product)
d.a = 10
print("product after change:", d.product)  # should recompute → 30

print("\n=== METHOD ERROR WRAPPING ===")
try:
	d.fail()
except ComputationError as e:
	print("Method error OK")
	print("key:", e.key)
	print("orig:", type(e.orig_exc).__name__)

print("\n=== WATCHERS ===")
events = []

def watcher(k, old, new):
	events.append((k, old, new))

d.watch(watcher)
d.b = 20
print("watch events:", events)

print("\n=== TRANSACTION COMMIT ===")
with d.transaction():
	d.a = 1
	d.b = 2
print("after commit:", d.a, d.b)

print("\n=== TRANSACTION ROLLBACK ===")
try:
	with d.transaction():
		d.a = 999
		raise RuntimeError("boom")
except RuntimeError:
	pass

print("after rollback:", d.a)  # should be unchanged

print("\n=== FREEZE ===")
d.freeze()
try:
	d.a = 123
except AttributeError:
	print("freeze OK")

print("\n=== ANTI FREEZE ===")
d2 = Data(
	x=1,
	y=AntiFreeze([1, 2, 3])
)
d2.freeze()
d2.y.append(4)
print("anti-freeze:", d2.y)

print("\n=== PATH GET / SET ===")
d3 = Data()
d3.set("foo.bar.baz", 42)
print("path get:", d3.get("foo.bar.baz"))
print("missing path:", d3.get("foo.nope", "default"))

print("\n=== DIFF / APPLY ===")
d4 = Data(a=1, b=2)
d5 = Data(a=1, b=99)
patch = d5.diff(d4)
print("patch:", patch)
d4.apply(patch)
print("after apply:", d4.a, d4.b)

print("\n=== SNAPSHOT ===")
snap = d4.snapshot()
d4.b = 500
print("snapshot preserved:", snap.b)

print("\n=== SERIALIZATION ===")
print(d4.to_dict())

print("\n=== VIEW ===")
v = d4.view({
	"double_a": lambda s: s.a * 2,
	"sum": lambda s: s.a + s.b
})
print("view.double_a:", v.double_a)
print("view.sum:", v.sum)

print("\n=== VIEW ERROR ===")
try:
	bad = d4.view({"oops": lambda s: 1 / 0})
	print(bad.oops)
except ComputationError:
	print("view error OK")

print("\n=== CIRCULAR SERIALIZATION ===")
c = Data()
c.self = c
print(c.to_dict())

print("\n=== METHOD BINDING STRESS ===")
for i in range(1000):
	x = Data(
		n=i,
		add=Method(lambda self, v: self.n + v)
	)
	if x.add(1) != i + 1:
		raise RuntimeError("method binding failed")

print("method binding OK")

print("\n=== MASS OBJECT STRESS ===")
objs = []
for i in range(1000):
	objs.append(Data(
		i=i,
		sq=Computed(lambda s: s.i * s.i),
		add=Method(lambda s, v: s.i + v),
		lz=Lazy(lambda s: s.i + 1)
	))

total = 0
for o in objs:
	total += o.sq
	total += o.add(1)
	total += o.lz

print("mass total:", total)

print("\n=== ALL TESTS PASSED ===")
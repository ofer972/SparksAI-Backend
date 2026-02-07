"""
Benchmark: settings property + _get(key) vs hardcoded constant.
Run from SparksAI-Backend: python benchmark_settings_access.py
"""
import timeit

# Simulate current pattern: module-level _cache + _get + property
_cache = {"backend_default_query_limit": 300}
_DEFAULTS = {"backend_default_query_limit": 300}


def _get(key: str):
    if key in _cache:
        return _cache[key]
    return _DEFAULTS.get(key)


class Settings:
    @property
    def DEFAULT_QUERY_LIMIT(self):
        return _get("backend_default_query_limit")


settings = Settings()

# Hardcoded constant (what you'd have if everything was module-level)
HARDCODED_LIMIT = 300

# Direct dict lookup (no property, no _get call)
def direct_lookup():
    return _cache["backend_default_query_limit"]


def use_settings_property():
    return settings.DEFAULT_QUERY_LIMIT


def use_hardcoded():
    return HARDCODED_LIMIT


def use_direct_dict():
    return direct_lookup()


N = 1_000_000  # 1 million accesses

t_prop = timeit.timeit(use_settings_property, number=N)
t_hard = timeit.timeit(use_hardcoded, number=N)
t_dict = timeit.timeit(use_direct_dict, number=N)

print("1 million accesses each:")
print(f"  Hardcoded constant:     {t_hard:.4f}s  ({t_hard/N*1e9:.0f} ns/access)")
print(f"  Direct dict lookup:     {t_dict:.4f}s  ({t_dict/N*1e9:.0f} ns/access)")
print(f"  settings.X (property): {t_prop:.4f}s  ({t_prop/N*1e9:.0f} ns/access)")
print()
print(f"  Overhead of property vs hardcoded: {(t_prop - t_hard)/t_hard*100:.0f}%")
print(f"  Extra time per access: {(t_prop - t_hard)/N*1e9:.0f} ns")
print()
print("  Conclusion: difference is in the hundreds of nanoseconds per access.")
print("  Even 1000 settings accesses per request ~0.1-0.3 ms vs DB/network (ms).")

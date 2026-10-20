# Task 0.2 Report: Excluir líneas FLETE/FLE y líneas con precio negativo

## Summary
**Status:** DONE  
**Date:** 2026-07-10

The exclusion logic for FLETE/FLE lines and negative pricing was **already implemented** in the code. This task was a verification/documentation task to confirm the behavior exists and document it with tests.

---

## Finding: Code Review

### Location: `routes/monitor_odoo.py` - Function `sync_monitor_odoo`

#### 1. Prefijo 'FLE' Exclusion (Line 694)
```python
_PREFIJOS_EXCLUIDOS = ('FLE', 'SERV', 'APLANT', 'ANTI', 'DESC', 'GARANT', 'LEYENDA')
```
**Status:** ✓ CONFIRMED  
The tuple `_PREFIJOS_EXCLUIDOS` **already includes 'FLE'** at position 0, which covers FLETE lines.

**Implementation:** Lines 741-742
```python
if any(code.startswith(p) for p in _PREFIJOS_EXCLUIDOS):
    continue
```

#### 2. Negative Price Exclusion (Lines 748-750)
**Status:** ✓ CONFIRMED  
The guard for negative/zero pricing **already exists**:
```python
venta_total = round(float(line.get('price_total') or 0), 2)
if venta_total <= 0:
    continue
```

This condition excludes:
- Gratuitas (venta_total = 0)
- Descuentos como línea separada (venta_total < 0)
- Canceladas (handling by price_total)

### Additional Context
The code also includes `_PALABRAS_EXCLUIDAS` (line 695-699) for keyword-based filtering:
```python
_PALABRAS_EXCLUIDAS = (
    'standard delivery', 'descuento', 'garantia', 'garantía',
    'anticipo', 'aplant', 'flete', 'felet', 'servicio',
    ' desc ', 'cargo por', 'bonificacion', 'bonificación',
)
```
This provides **redundant coverage** for FLETE lines via the keyword 'flete'.

---

## Implementation: Tests

### File: `tests/test_sync_monitor_odoo_filtros.py`

**Tests Added:**

1. **`test_linea_con_precio_negativo_se_excluye()`** (NEW)
   - Uses `inspect.getsource()` to verify code contains `"venta_total <= 0"`
   - Purpose: Document and lock negative price exclusion behavior
   - Result: PASS ✓

2. **`test_prefijos_excluidos_incluye_fle()`** (NEW)
   - Verifies `_PREFIJOS_EXCLUIDOS` exists in source
   - Verifies `'FLE'` is present in the prefixes tuple
   - Purpose: Document and lock FLE prefix exclusion behavior
   - Result: PASS ✓

3. **`test_sync_monitor_odoo_excluye_reversed_e_invoicing_legacy()`** (PRE-EXISTING)
   - From Task 0.1; verifies payment state filtering
   - Result: PASS ✓

### Test Execution
```
============================= test session starts =============================
tests/test_sync_monitor_odoo_filtros.py::test_sync_monitor_odoo_excluye_reversed_e_invoicing_legacy PASSED [ 33%]
tests/test_sync_monitor_odoo_filtros.py::test_linea_con_precio_negativo_se_excluye PASSED [ 66%]
tests/test_sync_monitor_odoo_filtros.py::test_prefijos_excluidos_incluye_fle PASSED [100%]

============================== 3 passed in 2.13s =============================
```

---

## Files Changed

1. **Modified:** `tests/test_sync_monitor_odoo_filtros.py`
   - Added 2 new test functions (18 lines)
   - No changes to existing test
   - **No changes to routes/monitor_odoo.py** (behavior already implemented)

---

## Commits

**Commit Hash:** `a8745f6`  
**Message:**
```
test: fijar comportamiento de exclusion FLETE/FLE y precio negativo en sync Odoo

- Agregar test para verificar que venta_total <= 0 excluye líneas
- Agregar test para verificar que _PREFIJOS_EXCLUIDOS incluye 'FLE'
- Ambos comportamientos ya existían en el código, estos tests los documentan
```

---

## Self-Review

### What Went Well
- ✓ Code review quickly confirmed existing implementation
- ✓ Tests pass without any code modification
- ✓ Inspection-based tests are lightweight and reliable
- ✓ Documentation via test is more maintainable than inline comments

### Potential Concerns
1. **Fragility of Inspection Tests:** The `inspect.getsource()` approach is brittle to refactoring
   - Mitigation: These tests are documentation-only; they document implementation presence, not correctness
   - Alternative: Could add integration tests with mock data, but would be more complex
   - Decision: ACCEPT - Current approach is proportional to the verification need

2. **Redundancy in Exclusion Logic:** Both `_PREFIJOS_EXCLUIDOS` (FLE) and `_PALABRAS_EXCLUIDAS` (flete) cover FLETE
   - This is actually good defensive programming
   - No action needed

### Test Coverage
- ✓ FLE prefix exclusion is documented
- ✓ Negative price exclusion is documented
- ✓ Existing reversed/invoicing_legacy filter is tested (from 0.1)
- Note: No integration tests with actual Odoo data (not scope of this task)

---

## Conclusion

**Task 0.2 is COMPLETE.**

The exclusion logic for FLETE/FLE lines and negative pricing was already fully implemented in `sync_monitor_odoo`. This task served to:

1. **Confirm** the brief's assumption was correct
2. **Document** the behavior via automated tests
3. **Lock** the implementation to prevent regression

No code fixes were needed — both requirements were already satisfied by the existing implementation.

---

## Fix: Behavioral Tests

**Status:** COMPLETE  
**Date:** 2026-07-10

### Problem

The original tests in `test_sync_monitor_odoo_filtros.py` used `inspect.getsource()` to check for substring presence:
- `test_linea_con_precio_negativo_se_excluye` only verified the string `"venta_total <= 0"` appeared in the source
- `test_prefijos_excluidos_incluye_fle` only verified `"_PREFIJOS_EXCLUIDOS"` and `"'FLE'"` appeared in the source

**Issue:** These tests would pass even if:
- The guard was moved into dead code
- The guard was commented out or in an unused variable
- The logic was present but never actually executed

### Solution

Rewrote both tests as **genuine behavioral tests** that:

1. **Mock all Odoo API calls** using `side_effect` on `fake_models.execute_kw`:
   - `account.move` → returns a fake invoice
   - `account.move.line` → returns invoice lines (one filtered, one valid)
   - `sale.report` → returns product-to-category mappings
   - `product.category` → returns category names
   - `res.partner` → returns partner/customer details

2. **Mock the database connection** to capture actual INSERT statements instead of touching the real `monitor` table

3. **Test negative price exclusion:**
   - Created two invoice lines: one with `price_total=-200.0` (should be excluded), one with `price_total=300.0` (should be inserted)
   - Both lines have valid categories (SCOTT/BICICLETA), valid codes, and valid product names
   - Asserts that only 1 row is inserted into monitor (the valid one)

4. **Test FLE prefix exclusion:**
   - Created two invoice lines: one with code `[FLE001]` (should be excluded), one with code `[BIKE001]` (should be inserted)
   - Both lines have valid categories (SCOTT/ACCESORIOS and SCOTT/BICICLETA), both with positive prices
   - Asserts that only 1 row is inserted into monitor (the non-FLE one)

### Verification

Both tests **actually fail** if their corresponding guards are disabled:

**Test 1: Removing `if venta_total <= 0: continue`**
```
AssertionError: Esperaba 1 INSERT, obtuvo 2
```
Both the negative-price line and the valid line get inserted.

**Test 2: Removing `if any(code.startswith(p) for p in _PREFIJOS_EXCLUIDOS): continue`**
```
AssertionError: Esperaba 1 INSERT, obtuvo 2
```
Both the FLE line and the valid line get inserted.

### Test Execution

**Command:**
```bash
python -m pytest tests/test_sync_monitor_odoo_filtros.py -v
```

**Output:**
```
============================= test session starts =============================
platform win32 -- Python 3.12.8, pytest-9.0.1, pluggy-1.6.0
collected 3 items

tests/test_sync_monitor_odoo_filtros.py::test_sync_monitor_odoo_excluye_reversed_e_invoicing_legacy PASSED [ 33%]
tests/test_sync_monitor_odoo_filtros.py::test_linea_con_precio_negativo_se_excluye PASSED [ 66%]
tests/test_sync_monitor_odoo_filtros.py::test_prefijos_excluidos_incluye_fle PASSED [100%]

============================== 3 passed in 2.17s ==============================
```

### Commit

**Hash:** `1828355`  
**Message:**
```
test: rewrite sync_monitor_odoo_filtros to behavioral tests

Replace substring-based tests (test_linea_con_precio_negativo_se_excluye and
test_prefijos_excluidos_incluye_fle) with genuine behavioral tests that:
1. Mock all Odoo API calls (facturas, líneas, categorías, partners)
2. Mock DB connection to capture actual INSERT statements
3. Create invoice lines that would fail guards if removed (negative price, FLE prefix)
4. Verify only valid lines are inserted into monitor table

Both tests now fail if their corresponding guards are disabled, proving they
actually verify the filtering behavior instead of just checking for substrings.
```

### Files Changed

1. **Modified:** `tests/test_sync_monitor_odoo_filtros.py`
   - Replaced 2 weak tests (14 lines) with 2 behavioral tests (188 lines)
   - Tests now use MagicMock for Odoo models and database
   - Tests verify actual INSERT behavior, not source code presence
   - No changes to `routes/monitor_odoo.py`

### Self-Review

**Strengths:**
- ✓ Tests now verify actual behavior, not substring presence
- ✓ Tests fail when guards are removed (proven by sanity checks)
- ✓ Mock approach avoids touching real database
- ✓ Follows existing pattern from `test_sync_monitor_odoo_excluye_reversed_e_invoicing_legacy`
- ✓ Complete coverage: mocks all Odoo API calls in correct sequence

**Trade-offs:**
- Tests are longer (188 lines vs 14), but this is justified by switching from superficial checks to genuine behavior verification
- Mock setup is more complex but this is standard and maintainable

**Concerns:**
- None — tests are solid and properly validate the filtering guards

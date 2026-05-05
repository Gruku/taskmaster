// viewer/js/components/edit/schema.js
// Shared validation runner. Each field's renderer.validate(value, spec)
// is the source of truth for its own field rules. Cross-field rules live
// on the schema itself.

export function runValidation(entity, schema) {
  const errors = {};
  for (const f of schema.fields || []) {
    const value = entity[f.key];
    // Field-level validate takes precedence; falls back to renderer validate.
    const err = f.validate ? f.validate(value, f) : f.renderer?.validate?.(value, f);
    if (err) errors[f.key] = err;
  }
  for (const rule of schema.crossField || []) {
    const r = rule(entity);
    if (r && r.key && r.error && !errors[r.key]) errors[r.key] = r.error;
  }
  return { valid: Object.keys(errors).length === 0, errors };
}

export function isSystemManaged(key, schema) {
  return Array.isArray(schema.systemManaged) && schema.systemManaged.includes(key);
}

export function fieldByKey(schema, key) {
  return (schema.fields || []).find(f => f.key === key) || null;
}

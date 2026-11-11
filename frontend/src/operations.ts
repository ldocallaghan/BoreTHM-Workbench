// Operations rows from the model's operation_defaults, as the
// operations-fields route returns them.
export type FieldRow = {name: string; label: string; quantity: string; unit: string | null; rule: string; source: string; default: unknown; restates_default: boolean};

// The value a row takes when it is first stated: the model's default where
// stating it gives the default result, and otherwise nothing, so the user
// enters a value. The default and the rule stay shown as text.
export function initial(row: FieldRow): unknown {
  return row.restates_default ? row.default : null;
}


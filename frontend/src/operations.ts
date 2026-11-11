// Operations rows from the model's operation_defaults, as the
// operations-fields route returns them.
export type FieldRow = {name: string; label: string; quantity: string; unit: string | null; rule: string; source: string; default: unknown; restates_default: boolean};

// The value a row takes when it is first stated: the model's default where
// stating it gives the default result, and otherwise nothing, so the user
// enters a value. The default and the rule stay shown as text.
export function initial(row: FieldRow): unknown {
  return row.restates_default ? row.default : null;
}

// The programme heading: the model's choice only when nothing is stated,
// neither a programme nor an operating value.
export function programmeHeading(statedProgramme: boolean, statedValues: number): string {
  return statedProgramme || statedValues > 0 ? 'Programme: stated values and model choices' : 'Programme chosen by the model';
}

// Operating values stated in a site document's [operations] table.
export function statedCount(operations: Record<string, unknown> | null | undefined): number {
  return Object.keys(operations ?? {}).filter(name => name !== 'sources').length;
}

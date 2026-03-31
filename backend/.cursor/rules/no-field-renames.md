# Rule: Do Not Rename Existing Class Fields

## Purpose
Ensure that Cursor must NOT rename any existing class, struct, or interface field names during refactoring, editing, or code generation. This rule applies to all programming languages supported by the project.

## Requirements
- Cursor MUST preserve the exact names of all existing class fields.
- Cursor MUST NOT:
  - Rename class fields.
  - Change casing or apply naming conventions to existing fields.
  - Replace fields with new identifiers.
  - Split or merge fields.
- Cursor MAY:
  - Modify logic that uses the fields, as long as field names stay unchanged.
  - Add new fields (only if explicitly requested by the user).
  - Rename local variables, function parameters, or method names, unless restricted by other rules.

## Detection Logic
- If the user requests refactoring, optimization, or cleanup:
  - Cursor must verify that field names are preserved.
  - If preserving field names conflicts with a refactor, the refactor must be adapted or explained.

## Examples

### ❌ Forbidden
- Renaming `firstName` → `givenName`
- Renaming `_count` → `count`
- Changing `user_id` → `userId`

### ✅ Allowed
- Updating methods that *use* the fields.
- Changing business logic while keeping fields intact.
- Adding new fields when explicitly asked by user.
- Renaming variables inside methods.

## If Violation Detected
Cursor should respond:
> “This change would rename an existing class field, which is prohibited by the project’s no-field-renames rule.”

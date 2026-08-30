/**
 * Each whitespace-separated word: first character uppercased, remaining characters lowercased.
 * Used for display titles (often described informally as "Pascal case" per word: "Hot Chocolate", "Горещ Шоколад").
 */
export function toPascalCaseWords(text: string): string {
  return text
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => {
      const first = word.charAt(0).toLocaleUpperCase()
      const rest = word.slice(1).toLocaleLowerCase()
      return first + rest
    })
    .join(' ')
}

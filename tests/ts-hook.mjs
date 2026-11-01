/* Resolve hook for the frontend tests.

   frontend/src is written in TypeScript and uses the extensionless relative
   import style Vite resolves for it ("./render", "./blocks"). Node's ESM
   resolver rejects a specifier with no extension outright, so the tests could
   not import the shipped source without either a build step or edits to the
   app's own import statements. This hook tries the TypeScript extension list
   before giving up, which keeps the import specifiers in the app unchanged.

   Bare specifiers such as "vue" are passed straight through: Node resolves them
   from the importing file's location, so frontend/src/data/stream.ts finds
   frontend/node_modules/vue without any mapping here. */

const EXTENSIONS = ['.ts', '.tsx', '.mts', '.js', '.mjs', '.json']

const HAS_EXTENSION = /\.[cm]?[jt]sx?$|\.json$/i

export async function resolve(specifier, context, nextResolve) {
  const isRelative = specifier.startsWith('./') || specifier.startsWith('../')
  if (!isRelative || HAS_EXTENSION.test(specifier)) {
    return nextResolve(specifier, context)
  }

  for (const ext of EXTENSIONS) {
    try {
      return await nextResolve(specifier + ext, context)
    } catch {
      // Not this extension; try the next one.
    }
  }

  return nextResolve(specifier, context)
}

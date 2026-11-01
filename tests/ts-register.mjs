/* Registers the resolve hook for one test process.

   run_all.mjs passes this file through `--import`, so every test_*.mjs child
   gets the same TypeScript resolution rules without duplicating the setup. */

import { register } from 'node:module'

register('./ts-hook.mjs', import.meta.url)

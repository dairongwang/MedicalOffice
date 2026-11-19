// Unpacks the Chromium bundled in @sparticuz/chromium (installed with `npm install` in e2e/)
// and prints {"path": ..., "args": [...]} as JSON for conftest.py. Standard Node, no network.
import chromium from "@sparticuz/chromium";
const path = await chromium.executablePath();
console.log(JSON.stringify({ path, args: chromium.args }));

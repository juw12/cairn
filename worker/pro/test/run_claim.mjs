// Test harness: runs the real Worker handler against a fake PayPal.
// stdin: JSON {signingKey, order, orderStatus, request: {orderId, email}, env?}
// stdout: JSON {status, body}
import { handle } from "../src/index.js";
import { PLANS } from "../src/license.js";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

if (input.plans) {
  process.stdout.write(JSON.stringify(PLANS));
  process.exit(0);
}

const calls = [];
async function fakePayPal(url, init = {}) {
  calls.push(String(url));
  if (String(url).endsWith("/v1/oauth2/token")) {
    return new Response(JSON.stringify({ access_token: "TEST-TOKEN" }), { status: 200 });
  }
  if (init.headers?.Authorization !== "Bearer TEST-TOKEN") return new Response("{}", { status: 401 });
  if (!input.order) return new Response("{}", { status: input.orderStatus || 404 });
  return new Response(JSON.stringify(input.order), { status: 200 });
}

const env = {
  SIGNING_KEY: JSON.stringify(input.signingKey),
  PAYPAL_CLIENT_ID: "client",
  PAYPAL_SECRET: "secret",
  PAYPAL_ENV: "sandbox",
  ...(input.env || {}),
};
const request = new Request("https://cairn-pro.test/claim", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(input.request),
});
const response = await handle(request, env, fakePayPal);
process.stdout.write(JSON.stringify({ status: response.status, body: await response.json(), calls }));

// The UI's server: Next.js, with one addition — the API learns which PC is calling (BACKLOG L16 S2).
//
// The browser reaches the API only through the `/api` rewrite (next.config.ts), so the API sees every call
// as coming from 127.0.0.1. Next's rewrite proxy adds no X-Forwarded-For and passes on one the browser sent,
// which anyone could forge. Here the header is overwritten with the socket's own address before Next sees
// the request; the API believes it only from this host (src/core/abuse.py).
import { createServer } from "node:http";
import next from "next";

// what `next dev` / `next start` listen on by default
const DEFAULT_PORT = 3000;
const port = Number.parseInt(process.env.PORT ?? "", 10) || DEFAULT_PORT;
const dev = process.env.NODE_ENV !== "production";

const httpServer = createServer((req, res) => {
  req.headers["x-forwarded-for"] = req.socket.remoteAddress ?? "";
  handle(req, res);
});
// httpServer: Next hooks its dev reload (WebSocket) onto this server's upgrade event
const app = next({ dev, port, httpServer });
const handle = app.getRequestHandler();

await app.prepare();
httpServer.listen(port, () => {
  console.log(`> UI on http://localhost:${port} (${dev ? "development" : "production"})`);
});

import { NextResponse, type NextRequest } from "next/server";

import { NONCE_HEADER, contentSecurityPolicy } from "@/lib/csp";

/** Every page response gets the Content-Security-Policy with a fresh nonce (BACKLOG L16 S4). Next reads the
 *  nonce from the request's policy header and puts it on its own scripts; app/layout.tsx reads NONCE_HEADER
 *  for the theme script. */
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const policy = contentSecurityPolicy(nonce, process.env.NODE_ENV === "development");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set(NONCE_HEADER, nonce);
  requestHeaders.set("Content-Security-Policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  matcher: [
    {
      // pages only: /api carries the API's own policy (web/headers.py); Next's own files, image optimiser and
      // dev reload socket (/_next/...) and the icon need none
      source: "/((?!api|_next|favicon.ico).*)",
      // a prefetch renders no page, so it needs no nonce
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};

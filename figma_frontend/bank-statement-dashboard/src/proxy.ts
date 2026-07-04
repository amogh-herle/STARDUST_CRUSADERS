import { NextResponse, type NextRequest } from 'next/server'

const SESSION_COOKIE = 'cidecode_session'

/**
 * Next.js proxy — runs on every matched request before rendering.
 *
 * Uses a custom cookie-based session (cidecode_session) set by our
 * username/password auth, instead of Supabase Auth tokens.
 */
export function proxy(request: NextRequest) {
  const session = request.cookies.get(SESSION_COOKIE)?.value
  const isLoggedIn = Boolean(session)

  const isLoginPage = request.nextUrl.pathname === '/login'
  const isAuthRoute = request.nextUrl.pathname.startsWith('/auth/')

  // Always allow auth routes through
  if (isAuthRoute) {
    return NextResponse.next()
  }

  // Not logged in → redirect to /login
  if (!isLoggedIn && !isLoginPage) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    return NextResponse.redirect(url)
  }

  // Already logged in → redirect away from /login
  if (isLoggedIn && isLoginPage) {
    const url = request.nextUrl.clone()
    url.pathname = '/'
    return NextResponse.redirect(url)
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
}

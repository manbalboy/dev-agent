import { NextRequest } from 'next/server';

const BACKEND_BASE = process.env.AGENTHUB_API_BASE ?? 'http://127.0.0.1:8321';

function buildTargetUrl(path: string[], search: string): string {
  const normalized = path.join('/');
  const base = BACKEND_BASE.endsWith('/') ? BACKEND_BASE.slice(0, -1) : BACKEND_BASE;
  // Next 내부 라우트(/api/*)를 백엔드도 동일한 /api/* 네임스페이스로 전달한다.
  return `${base}/api/${normalized}${search}`;
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const target = buildTargetUrl(path, request.nextUrl.search);
  const method = request.method.toUpperCase();
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('connection');
  headers.delete('content-length');

  const body =
    method === 'GET' || method === 'HEAD'
      ? undefined
      : await request.arrayBuffer();

  const upstream = await fetch(target, {
    method,
    headers,
    body,
    redirect: 'manual',
    cache: 'no-store',
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}

type RouteContext = {
  params: { path: string[] };
};

export async function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path);
}

export async function POST(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path);
}

export async function PUT(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path);
}

export async function PATCH(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path);
}

export async function DELETE(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context.params.path);
}

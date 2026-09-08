import { resolve } from 'node:path';

export function configuration(directory) {
  return {
    output: 'standalone',
    outputFileTracingRoot: resolve(directory, '../..'),
    poweredByHeader: false,
    async headers() {
      return [{source: '/:path*', headers: [
        {key: 'X-Content-Type-Options', value: 'nosniff'},
        {key: 'Referrer-Policy', value: 'no-referrer'},
        {key: 'X-Frame-Options', value: 'DENY'},
      ]}];
    },
  };
}

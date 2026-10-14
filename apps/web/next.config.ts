import type { NextConfig } from 'next';

const defaultApiUrl = process.env.NODE_ENV === 'production'
  ? 'https://relay-demo-api.onrender.com'
  : 'http://localhost:8000';

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || defaultApiUrl,
  },
};

export default nextConfig;

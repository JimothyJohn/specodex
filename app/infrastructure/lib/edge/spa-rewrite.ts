/**
 * CloudFront Function (viewer-request) for the S3 default behavior:
 * client-side routes (/build, /projects/<id>, …) are rewritten to
 * /index.html; anything whose last path segment has a file extension
 * (/assets/index-abc.js, /robots.txt, /sitemap.xml) is passed through.
 *
 * Replaces the distribution-level `errorResponses` (403/404 → 200
 * index.html). Those apply to EVERY behavior, including /api/*, so an
 * unknown API path or an API 403/404 reached the browser as a 200
 * text/html page — the frontend then failed with
 * `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`, and the
 * error page was cached at the edge for 5 minutes. Scoping the SPA
 * fallback to the S3 behavior lets API errors through untouched.
 *
 * Source is a string because CloudFront Functions run their own
 * restricted JS runtime (cloudfront-js-2.0), not Node; the unit test
 * evaluates this exact string.
 */
export const SPA_REWRITE_FUNCTION_CODE = `
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  var last = uri.substring(uri.lastIndexOf('/') + 1);
  if (last.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}
`;

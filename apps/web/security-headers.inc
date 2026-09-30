# Included in the server block AND in every location that sets its own add_header:
# nginx drops inherited add_header directives in any location that defines one.
add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy no-referrer always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;
add_header Cross-Origin-Opener-Policy same-origin always;
# Map tiles come from OpenStreetMap; everything else is same-origin.
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self' ws: wss:; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'self'; object-src 'none'; frame-ancestors 'none'" always;

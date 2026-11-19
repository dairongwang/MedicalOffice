# Office administration dashboard — static files behind nginx. Independent of the API, the main
# app and the staff dashboard.
#   docker build -t medic-office-admin apps/admin
#   docker run -p 4400:80 -e API_BASE=https://api.example.com medic-office-admin
FROM nginx:1.27-alpine
COPY index.html styles.css config.js /usr/share/nginx/html/
COPY js /usr/share/nginx/html/js
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.d/40-admin-config.sh
RUN chmod +x /docker-entrypoint.d/40-admin-config.sh
# API_BASE: origin of the Medic Office API ("" = same origin, e.g. behind one reverse proxy).
ENV API_BASE="" SHOW_DUMMY_HINT="false"
EXPOSE 80

# Staff dashboard — static files behind nginx. Independent of the API and the main app.
#   docker build -t medic-office-staff apps/staff
#   docker run -p 4300:80 -e API_BASE=https://api.example.com medic-office-staff
FROM nginx:1.27-alpine
COPY index.html styles.css config.js /usr/share/nginx/html/
COPY js /usr/share/nginx/html/js
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.d/40-staff-config.sh
RUN chmod +x /docker-entrypoint.d/40-staff-config.sh
# API_BASE: origin of the Medic Office API ("" = same origin, e.g. behind one reverse proxy).
ENV API_BASE="" SHOW_DUMMY_HINT="false"
EXPOSE 80

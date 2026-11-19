// Default configuration. serve.py and the container entrypoint overwrite this file's values;
// `?api=https://api.example.com` in the URL also works (saved in the browser).
window.ADMIN_CONFIG = {
  apiBase: '', // '' = same origin as this page (e.g. the API serving /admin/)
  showDummyHint: true, // show the dummy administrator credentials on the sign-in form (development)
};

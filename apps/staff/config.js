// Default configuration. serve.py and the container entrypoint overwrite this file's values;
// `?api=https://api.example.com` in the URL also works (saved in the browser).
window.STAFF_CONFIG = {
  apiBase: '', // '' = same origin as this page (e.g. the API serving /staff/)
  showDummyHint: true, // show the dummy admin credentials on the sign-in form (development)
};

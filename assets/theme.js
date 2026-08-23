// Plotly bakes colours into the rendered figure, so the server needs to know the
// colour scheme before it builds one. Push the media query result into the
// theme store on load and whenever it changes; app.py styles from there.
(function () {
    var query = window.matchMedia('(prefers-color-scheme: dark)');

    function publish(tries) {
        var dc = window.dash_clientside;
        try {
            // set_props needs the layout rendered, not just the bundle loaded.
            dc.set_props('theme', {data: query.matches ? 'dark' : 'light'});
        } catch (e) {
            if ((tries || 0) < 100) setTimeout(function () { publish((tries || 0) + 1); }, 50);
        }
    }

    query.addEventListener('change', function () { publish(0); });
    publish(0);
})();

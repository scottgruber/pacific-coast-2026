// Live conditions for a day page: air quality and weather at the day's
// endpoint, fetched on load and rendered into the conditions card.
//
// Air quality goes through api/airnow.php rather than being called directly,
// because an AirNow key can't be domain-restricted and is rate-limited per key
// — see that file's header. Weather comes straight from the National Weather
// Service, which needs no key and sends CORS headers, so it can stay
// client-side on an otherwise static site.
//
// Everything here is progressive enhancement: the card ships with working
// external links, and each section is only replaced once its fetch succeeds. A
// failed or slow request leaves the links in place rather than blanking the
// panel.
(function () {
  var NWS_POINTS = "https://api.weather.gov/points/";

  function el(tag, className, text) {
    var n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function cToF(c) {
    return c * 9 / 5 + 32;
  }

  function kmhToMph(k) {
    return k * 0.621371;
  }

  // AirNow's own category names double as the severity scale; map them to the
  // handful of states the card styles. Anything unrecognized renders unstyled
  // rather than guessing at a severity.
  function aqiModifier(category) {
    var known = {
      "Good": "good",
      "Moderate": "moderate",
      "Unhealthy for Sensitive Groups": "sensitive",
      "Unhealthy": "unhealthy",
      "Very Unhealthy": "very-unhealthy",
      "Hazardous": "hazardous",
    };
    return known[category] || "";
  }

  function renderAirQuality(root, d) {
    var box = el("div", "conditions-live conditions-live--aqi");

    var head = el("div", "conditions-live__head");
    var mod = aqiModifier(d.category);
    var value = el("span", "aqi-badge" + (mod ? " aqi-badge--" + mod : ""), String(d.aqi));
    head.appendChild(value);
    head.appendChild(el("span", "conditions-live__label", d.category + " · " + d.parameter));
    box.appendChild(head);

    // The station's identity and distance are the whole point of showing this:
    // AirNow's network is sparse inland along this route, and smoke varies
    // sharply over short distances, so a bare number would imply a precision
    // the reading doesn't have.
    var src = el("p", "conditions-live__source");
    src.appendChild(document.createTextNode(
      "Reported by " + d.reporting_area + ", " + d.distance_mi + " mi away"
    ));
    box.appendChild(src);

    if (d.far) {
      box.appendChild(el(
        "p",
        "conditions-live__warn",
        "Nearest station is over 20 mi from the route — treat as a rough regional signal, not conditions at the roadside."
      ));
    }

    box.appendChild(el("p", "conditions-live__meta", "Observed " + d.observed));

    // The proxy serves an expired reading rather than nothing when AirNow is
    // rate-limiting. Say so, or the number reads as current.
    if (d.stale) {
      var mins = Math.round((d.stale_age_s || 0) / 60);
      box.appendChild(el(
        "p",
        "conditions-live__warn",
        "Live update unavailable — showing the last reading" +
          (mins > 0 ? ", about " + mins + " min old." : ".")
      ));
    }

    root.replaceChildren(box);
  }

  function renderWeather(root, w) {
    var box = el("div", "conditions-live conditions-live--wx");

    var head = el("div", "conditions-live__head");
    head.appendChild(el("span", "wx-temp", Math.round(w.tempF) + "°F"));
    head.appendChild(el("span", "conditions-live__label", w.text || ""));
    box.appendChild(head);

    var stats = el("ul", "wx-stats");
    stats.setAttribute("role", "list");
    var rows = [
      ["Wind", w.windMph === null ? null : Math.round(w.windMph) + " mph" + (w.windDir ? " " + w.windDir : "")],
      ["Humidity", w.humidity === null ? null : Math.round(w.humidity) + "%"],
      ["Forecast", w.forecast || null],
    ];
    rows.forEach(function (r) {
      if (r[1] === null) return;
      var li = el("li");
      li.appendChild(el("span", "wx-stats__label", r[0]));
      li.appendChild(el("span", "wx-stats__value", r[1]));
      stats.appendChild(li);
    });
    box.appendChild(stats);

    if (w.station) {
      box.appendChild(el("p", "conditions-live__source", "Observed at " + w.station));
    }
    root.replaceChildren(box);
  }

  function loadAirQuality(root, lat, lon) {
    return fetch("api/airnow.php?lat=" + lat + "&lon=" + lon)
      .then(function (r) {
        if (!r.ok) throw new Error("aqi " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (d && d.error) throw new Error(d.error);
        renderAirQuality(root, d);
      })
      .catch(function () {
        // Leave the external link in place.
      });
  }

  function jsonFetch(url) {
    return fetch(url, { headers: { Accept: "application/geo+json" } }).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }

  // NWS needs three hops: the grid point for this lat/lon, then that grid's
  // forecast, then the nearest station's latest observation (the forecast
  // payload carries no humidity).
  function loadWeather(root, lat, lon) {
    return jsonFetch(NWS_POINTS + lat + "," + lon)
      .then(function (pt) {
        var p = pt.properties;
        return Promise.all([
          jsonFetch(p.forecast).catch(function () { return null; }),
          jsonFetch(p.observationStations).catch(function () { return null; }),
        ]);
      })
      .then(function (both) {
        var forecast = both[0];
        var stations = both[1];
        var period = forecast && forecast.properties.periods[0];

        var obsUrl = null;
        var stationName = null;
        if (stations && stations.features && stations.features.length) {
          var s = stations.features[0].properties;
          stationName = s.name || s.stationIdentifier;
          obsUrl = "https://api.weather.gov/stations/" + s.stationIdentifier + "/observations/latest";
        }
        if (!obsUrl) return { period: period, obs: null, stationName: null };
        return jsonFetch(obsUrl)
          .then(function (o) { return { period: period, obs: o, stationName: stationName }; })
          .catch(function () { return { period: period, obs: null, stationName: null }; });
      })
      .then(function (r) {
        var p = r.period;
        var op = r.obs && r.obs.properties;
        var val = function (k) { return op && op[k] && op[k].value !== null ? op[k].value : null; };

        // Prefer the station's live observation; fall back to the forecast
        // period when a field isn't being reported.
        var tempC = val("temperature");
        var tempF = tempC !== null ? cToF(tempC)
          : (p && p.temperatureUnit === "F" ? p.temperature : null);
        if (tempF === null) throw new Error("no temperature");

        var windKmh = val("windSpeed");
        renderWeather(root, {
          tempF: tempF,
          text: (op && op.textDescription) || (p && p.shortForecast) || "",
          windMph: windKmh !== null ? kmhToMph(windKmh) : null,
          windDir: p ? p.windDirection : null,
          humidity: val("relativeHumidity"),
          forecast: p ? p.name + ": " + p.shortForecast + ", " + p.temperature + "°" + p.temperatureUnit : null,
          station: r.stationName,
        });
      })
      .catch(function () {
        // Leave the external links in place.
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var card = document.querySelector("[data-conditions]");
    if (!card) return;
    var lat = card.getAttribute("data-lat");
    var lon = card.getAttribute("data-lon");
    if (!lat || !lon) return;

    var aqiSlot = card.querySelector("[data-conditions-aqi]");
    var wxSlot = card.querySelector("[data-conditions-weather]");
    if (aqiSlot) loadAirQuality(aqiSlot, lat, lon);
    if (wxSlot) loadWeather(wxSlot, lat, lon);
  });
})();

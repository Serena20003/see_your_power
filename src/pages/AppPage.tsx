import { type FormEvent, useEffect, useRef, useState } from "react";
import { Button, TextInput, Theme } from "@carbon/react";
import { Flash, Search } from "@carbon/icons-react";
import { Link, useNavigate } from "react-router-dom";
import ForecastPanel from "../ForecastPanel";
import { useAuth } from "../lib/supabase/AuthContext";
import { geocodeAddress } from "../lib/mapbox/geocode";

export default function AppPage() {
  const { session, profile: userProfile } = useAuth();
  const navigate = useNavigate();
  const redirectedRef = useRef(false);

  // Supabase appends #error=access_denied&error_description=... to the redirect
  // URL when a magic link is invalid or expired. Detect it on mount and forward
  // the user to /login so they see a proper error message.
  //
  // Also handle the successful magic-link redirect: if the session exists but
  // the profile is incomplete, send new users to /onboarding.
  useEffect(() => {
    const hash = window.location.hash;
    if (hash.includes('error=')) {
      window.history.replaceState(null, '', window.location.pathname);
      window.location.replace('/login?error=1');
      return;
    }

    // Only run once per mount; skip if no session yet (AuthContext still loading).
    if (!session || redirectedRef.current) return;
    redirectedRef.current = true;

    // If the user has no first_name they haven't completed onboarding.
    const profileIncomplete = !userProfile?.first_name?.trim();
    if (profileIncomplete) {
      navigate('/onboarding');
    }
  }, [session, userProfile, navigate]);

  const initials =
    session && userProfile?.first_name && userProfile?.last_name
      ? `${userProfile.first_name[0]}${userProfile.last_name[0]}`.toUpperCase()
      : null;

  // County FIPS — default to Los Angeles for the demo
  const [fips, setFips] = useState("06037");

  // Address search state
  const [addressInput, setAddressInput] = useState("");
  const [flyToCoords, setFlyToCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [geocodeError, setGeocodeError] = useState<string | null>(null);
  const [geocoding, setGeocoding] = useState(false);

  async function handleAddressSearch(e: FormEvent) {
    e.preventDefault();
    const query = addressInput.trim();
    if (!query) return;
    setGeocodeError(null);
    setGeocoding(true);
    try {
      const result = await geocodeAddress(query);
      setFlyToCoords({ lat: result.lat, lon: result.lon });
    } catch (err) {
      setGeocodeError(err instanceof Error ? err.message : "Address not found.");
    } finally {
      setGeocoding(false);
    }
  }

  return (
    <Theme theme="g10">
      <div className="site-shell">
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark"><Flash size={20} /></span>
            <span>runway<span className="brand-period">.</span></span>
          </div>
          <form className="header-address-search" onSubmit={handleAddressSearch} role="search" aria-label="Address search">
            <TextInput
              id="header-address-input"
              labelText=""
              hideLabel
              placeholder="Search address or zip code…"
              value={addressInput}
              onChange={(e) => setAddressInput(e.target.value)}
              disabled={geocoding}
              invalid={!!geocodeError}
              invalidText={geocodeError ?? undefined}
              size="sm"
            />
            <Button
              type="submit"
              kind="ghost"
              size="sm"
              renderIcon={Search}
              iconDescription="Search"
              hasIconOnly
              disabled={geocoding || !addressInput.trim()}
            />
          </form>
          <div className="header-right">
            <a className="header-model-link" href="/model-sources">Model sources ↗</a>
            {initials ? (
              <Link to="/profile" className="avatar" aria-label="Go to profile" style={{ textDecoration: 'none' }}>
                {initials}
              </Link>
            ) : (
              <Button kind="ghost" size="sm" onClick={() => { window.location.href = '/login'; }}>
                Log in / Sign up
              </Button>
            )}
          </div>
        </header>

        <main>
          <div className="welcome">
            {session && userProfile?.first_name && (
              <p>Hello, {userProfile.first_name}. Here is what the next few days may mean for your power.</p>
            )}
          </div>

          {/* Forecast section */}
          <div id="forecast">
            <ForecastPanel fips={fips} onFipsChange={setFips} flyToCoords={flyToCoords} />
          </div>

          {/* Plan panel — wired in sub-task 9 */}
          {session !== null && (
            <>{/* Plan panel — wired in sub-task 9 */}</>
          )}

          {/* Assistant panel — wired in sub-task 9 */}
          {session !== null && (
            <>{/* Assistant panel — wired in sub-task 9 */}</>
          )}
        </main>

        <footer className="site-footer">
          <span>Runway · Static product preview</span>
          <span>
            County forecast data is sample output from an unvalidated model stub.
            No numeric outage probabilities are presented as validated predictions.
          </span>
        </footer>
      </div>
    </Theme>
  );
}

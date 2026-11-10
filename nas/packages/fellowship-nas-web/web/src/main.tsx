import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import { Session, currentSession, setSession } from "./api";
import { apply, loadLook, savedMode, savedPalette } from "./appearance";
import { Login } from "./desktop/login";
import { Desktop } from "./desktop/shell";
import { SessionContext } from "./settings";

function Root() {
    // undefined while asking the server whether this browser is still logged in
    const [session, setCurrent] = useState<Session | null | undefined>(undefined);
    setSession(session || null, () => setCurrent(null));

    // Their theme and light or dark are on the NAS, so they arrive with them.
    // The session goes in first, since asking for it needs one, and the
    // desktop only draws once the look is right.
    const signedIn = (found: Session | null) => {
        if (!found) {
            setCurrent(null);
            return;
        }
        setSession(found, () => setCurrent(null));
        loadLook(found.user).catch(() => {}).then(() => setCurrent(found));
    };

    useEffect(() => {
        currentSession().then(signedIn, () => setCurrent(null));
    }, []);

    if (session === undefined) {
        return <div class="desktop-area" />;
    }
    if (!session) {
        return <Login onLogin={signedIn} />;
    }
    return (
        <SessionContext.Provider value={session}>
            <Desktop onSignedOut={() => setCurrent(null)} />
        </SessionContext.Provider>
    );
}

// Before anything draws, so the login screen wears the chosen look too
apply(savedMode(), savedPalette());
render(<Root />, document.getElementById("app")!);

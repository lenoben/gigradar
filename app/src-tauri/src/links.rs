//! `gigradar://` links: what a notification click (or the tray) hands to the app.
//!
//! The scheme is registered for the current user by the installer. A click on a toast starts the app with the link as
//! its only argument (or, when the app is already running, starts a second copy that passes the link on and exits).
//! Everything after the scheme is untrusted text: only the three shapes below are accepted, with strict characters,
//! and the window gets a small JSON value, never the raw link.

use serde_json::{json, Value};

pub const SCHEME: &str = "gigradar";
const MAX_RUN_CHARS: usize = 48;
const MAX_ID_CHARS: usize = 64;

#[derive(Debug, PartialEq, Eq)]
pub enum Link {
    /// gigradar://job/<id>: one job's card.
    Job(String),
    /// gigradar://jobs?run=<time stamp>: the jobs one run announced (no run: the latest jobs).
    Jobs(Option<String>),
    /// gigradar://status
    Status,
}

impl Link {
    pub fn to_value(&self) -> Value {
        match self {
            Link::Job(id) => json!({ "kind": "job", "id": id }),
            Link::Jobs(run) => json!({ "kind": "jobs", "run": run }),
            Link::Status => json!({ "kind": "status" }),
        }
    }
}

/// The first argument that is a valid link, if any.
pub fn find_link<I: IntoIterator<Item = String>>(args: I) -> Option<Link> {
    args.into_iter().find_map(|arg| parse(&arg))
}

pub fn parse(raw: &str) -> Option<Link> {
    let (scheme, rest) = raw.trim().split_once("://")?;
    if !scheme.eq_ignore_ascii_case(SCHEME) {
        return None;
    }
    let (path, query) = match rest.split_once('?') {
        Some((path, query)) => (path, Some(query)),
        None => (rest, None),
    };
    match path.trim_end_matches('/') {
        "status" => Some(Link::Status),
        "jobs" => {
            let run = query.and_then(|q| q.split('&').find_map(|pair| pair.strip_prefix("run=")));
            match run {
                None => Some(Link::Jobs(None)),
                Some(value) => {
                    let run = percent_decode(value)?;
                    valid_run(&run).then_some(Link::Jobs(Some(run)))
                }
            }
        }
        other => {
            let id = percent_decode(other.strip_prefix("job/")?)?;
            valid_id(&id).then_some(Link::Job(id))
        }
    }
}

/// A job id is the `~` followed by letters and digits (Upwork's ciphertext).
pub fn valid_id(id: &str) -> bool {
    id.len() <= MAX_ID_CHARS && id.strip_prefix('~').is_some_and(|rest| !rest.is_empty() && rest.chars().all(|c| c.is_ascii_alphanumeric()))
}

/// A run is the ISO time stamp the helper marked its jobs with: digits and `T : . + - Z` only.
pub fn valid_run(run: &str) -> bool {
    !run.is_empty() && run.len() <= MAX_RUN_CHARS && run.chars().all(|c| c.is_ascii_digit() || matches!(c, 'T' | ':' | '.' | '+' | '-' | 'Z'))
}

fn percent_decode(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hex = text.get(i + 1..i + 3)?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_three_shapes_a_notification_carries() {
        assert_eq!(parse("gigradar://job/~0123abc"), Some(Link::Job("~0123abc".into())));
        assert_eq!(parse("gigradar://job/%7E0123abc/"), Some(Link::Job("~0123abc".into())));
        assert_eq!(parse("GigRadar://status"), Some(Link::Status));
        assert_eq!(
            parse("gigradar://jobs?run=2026-10-09T10%3A00%3A00%2B00%3A00"),
            Some(Link::Jobs(Some("2026-10-09T10:00:00+00:00".into())))
        );
        assert_eq!(parse("gigradar://jobs"), Some(Link::Jobs(None)));
        assert_eq!(parse("gigradar://jobs?run=2026-10-09T10:00:00.123456+00:00"), Some(Link::Jobs(Some("2026-10-09T10:00:00.123456+00:00".into()))));
    }

    #[test]
    fn anything_else_is_ignored() {
        for bad in [
            "",
            "https://example.com/job/~01",
            "gigradar:job/~01",
            "gigradar://",
            "gigradar://other",
            "gigradar://job/",
            "gigradar://job/01",
            "gigradar://job/~",
            "gigradar://job/~01;calc",
            "gigradar://job/~01/../x",
            "gigradar://job/~%zz",
            "gigradar://job/~%ff",
            "gigradar://jobs?run=a%20b",
            "gigradar://jobs?run=2026;rm",
            "gigradar://jobs?run=",
        ] {
            assert_eq!(parse(bad), None, "{bad}");
        }
        let long = format!("gigradar://job/~{}", "a".repeat(65));
        assert_eq!(parse(&long), None);
    }

    #[test]
    fn the_first_link_among_the_arguments_wins() {
        let args = ["app.exe", "--flag", "gigradar://status", "gigradar://job/~01"].map(String::from);
        assert_eq!(find_link(args), Some(Link::Status));
        assert_eq!(find_link(["app.exe".to_string()]), None);
    }

    #[test]
    fn the_window_gets_small_json() {
        assert_eq!(Link::Job("~01".into()).to_value(), json!({"kind": "job", "id": "~01"}));
        assert_eq!(Link::Jobs(None).to_value(), json!({"kind": "jobs", "run": null}));
        assert_eq!(Link::Status.to_value(), json!({"kind": "status"}));
    }
}

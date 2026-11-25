use std::collections::{HashMap, HashSet};

use anyhow::{Context, Result, ensure};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Event {
    pub bar: f64,
    pub bpm: Option<f64>,
    pub bar_length: Option<f64>,
    pub sentence_length: Option<u32>,
    pub section: Option<String>,
    pub text: Option<String>,
}

impl Event {
    fn merge(&mut self, next: &Self) {
        self.bar = next.bar;
        self.bpm = next.bpm.or(self.bpm);
        self.bar_length = next.bar_length.or(self.bar_length);
        self.sentence_length = next.sentence_length.or(self.sentence_length);
        if next.section.is_some() {
            self.section.clone_from(&next.section);
        }
        if next.text.is_some() {
            self.text.clone_from(&next.text);
        }
    }

    fn seconds_per_bar(&self) -> f64 {
        self.bar_length.unwrap_or(4.) * 60. / self.bpm.unwrap_or(120.)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Note {
    pub bar: f64,
    pub lane: u8,
    pub width: u8,
    pub kind: u8,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Tap(pub Note);

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Directional {
    pub note: Note,
    pub tap: Option<Tap>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Slide {
    pub note: Note,
    pub channel: u8,
    pub tap: Option<Tap>,
    pub directional: Option<Directional>,
    pub next: Option<usize>,
    pub head: Option<usize>,
}

impl Slide {
    pub fn is_path_note(&self) -> bool {
        self.note.kind != 0
            && (self.note.kind != 3 || self.directional.is_some() || self.tap.is_none())
    }

    pub fn is_among_note(&self) -> bool {
        self.note.kind == 3
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ScoreNote {
    Tap(Tap),
    Directional(Directional),
    Slide(Slide),
}

impl ScoreNote {
    pub fn note(&self) -> Note {
        match *self {
            Self::Tap(Tap(note))
            | Self::Directional(Directional { note, .. })
            | Self::Slide(Slide { note, .. }) => note,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Score {
    events: Vec<Event>,
    notes: Vec<ScoreNote>,
    note_events: Vec<Event>,
    timeline: Vec<(f64, Event)>,
}

impl Score {
    pub fn parse(sus: &str) -> Result<Self> {
        let records: Vec<_> = sus
            .lines()
            .filter_map(|line| {
                let (header, data) = line.trim().strip_prefix('#')?.split_once(':')?;
                Some((header, data.trim()))
            })
            .collect();

        let positive = |value: &str| -> Result<f64> {
            let number: f64 = value.parse()?;
            ensure!(
                number.is_finite() && number > 0.,
                "invalid SUS timing value: {value}"
            );
            Ok(number)
        };
        let mut bpms = HashMap::new();
        for &(header, data) in &records {
            if let Some(id) = header.strip_prefix("BPM").filter(|id| id.len() == 2) {
                bpms.insert(id.to_ascii_uppercase(), positive(data)?);
            }
        }

        let mut events = Vec::new();
        let mut notes = Vec::new();
        for (header, data) in records {
            let Some((measure, track)) = header.split_at_checked(3) else {
                continue;
            };
            if !measure.bytes().all(|b| b.is_ascii_digit()) {
                continue;
            }
            let measure: f64 = measure.parse()?;
            match track.as_bytes() {
                b"02" => events.push(Event {
                    bar: measure,
                    bar_length: Some(positive(data)?),
                    ..Event::default()
                }),
                b"08" => {
                    for (beat, pair) in parse_data(data)? {
                        let id = pair.to_ascii_uppercase();
                        let bpm = *bpms
                            .get(&id)
                            .with_context(|| format!("undefined SUS BPM {id}"))?;
                        events.push(Event {
                            bar: measure + beat,
                            bpm: Some(bpm),
                            ..Event::default()
                        });
                    }
                }
                [b'1' | b'5', lane] | [b'3', lane, _] => {
                    let lane = digit(*lane)?;
                    for (beat, pair) in parse_data(data)? {
                        let bytes = pair.as_bytes();
                        let note = Note {
                            bar: measure + beat,
                            lane,
                            width: digit(bytes[1])?,
                            kind: digit(bytes[0])?,
                        };
                        notes.push(match track.as_bytes() {
                            [b'1', ..] => ScoreNote::Tap(Tap(note)),
                            [b'5', ..] => ScoreNote::Directional(Directional { note, tap: None }),
                            [_, _, channel] => ScoreNote::Slide(Slide {
                                note,
                                channel: digit(*channel)?,
                                tap: None,
                                directional: None,
                                next: None,
                                head: None,
                            }),
                            _ => unreachable!(),
                        });
                    }
                }
                _ => {}
            }
        }
        Ok(Self::new(events, notes))
    }

    pub fn new(events: Vec<Event>, notes: Vec<ScoreNote>) -> Self {
        let (notes, note_events) = parse_notes(notes, true);
        Self::from_parts(events, notes, note_events)
    }

    fn from_parts(mut events: Vec<Event>, notes: Vec<ScoreNote>, note_events: Vec<Event>) -> Self {
        events.extend(note_events.iter().cloned());
        let events = merge_events(events);
        let mut current = Event {
            bar: events.first().map_or(0., |event| event.bar.min(0.)),
            bpm: Some(120.),
            bar_length: Some(4.),
            sentence_length: Some(4),
            ..Event::default()
        };
        // 前奏没有单独定义 BPM 时，沿用起始拍子的 BPM
        if !events
            .iter()
            .any(|event| event.bar < 0. && (event.bpm.is_some() || event.bar_length.is_some()))
            && let Some(start) = events.iter().find(|event| event.bar == 0.)
        {
            current.bpm = start.bpm.or(current.bpm);
            current.bar_length = start.bar_length.or(current.bar_length);
        }
        let mut time = current.bar * current.seconds_per_bar();
        let mut timeline = vec![(time, current.clone())];
        for event in &events {
            time += (event.bar - current.bar) * current.seconds_per_bar();
            current.merge(event);
            timeline.push((time, current.clone()));
        }
        let origin = timeline.partition_point(|(_, event)| event.bar <= 0.) - 1;
        let (time, event) = &timeline[origin];
        let shift = time - event.bar * event.seconds_per_bar();
        if shift != 0. {
            for (time, _) in &mut timeline {
                *time -= shift;
            }
        }
        Self {
            events,
            notes,
            note_events,
            timeline,
        }
    }

    pub fn events(&self) -> &[Event] {
        &self.events
    }
    pub fn notes(&self) -> &[ScoreNote] {
        &self.notes
    }

    pub fn get_time_event(&self, bar: f64) -> (f64, &Event) {
        let index = self
            .timeline
            .partition_point(|(_, event)| event.bar <= bar + 1e-6);
        let (time, event) = &self.timeline[index.saturating_sub(1)];
        (time + (bar - event.bar) * event.seconds_per_bar(), event)
    }
    pub fn get_time(&self, bar: f64) -> f64 {
        self.get_time_event(bar).0
    }
    pub fn get_event(&self, bar: f64) -> &Event {
        self.get_time_event(bar).1
    }
    pub fn get_time_delta(&self, from: f64, to: f64) -> f64 {
        self.get_time(to) - self.get_time(from)
    }
    pub fn get_bar_event(&self, seconds: f64) -> (f64, &Event) {
        let index = self.timeline.partition_point(|(time, _)| *time <= seconds);
        let (time, event) = &self.timeline[index.saturating_sub(1)];
        (
            event.bar + (seconds - time) / event.seconds_per_bar(),
            event,
        )
    }
    pub fn get_bar(&self, seconds: f64) -> f64 {
        self.get_bar_event(seconds).0
    }

    pub fn rebase(&self, events: Vec<Event>, offset: f64) -> Self {
        let target = Self::new(events.clone(), vec![]);
        let bar = |bar| target.get_bar(self.get_time(bar) - offset);
        let notes = self
            .notes
            .iter()
            .map(|note| {
                let map = |mut note: Note| {
                    note.bar = bar(note.bar);
                    note
                };
                let tap = |Tap(note)| Tap(map(note));
                let directional = |note: Directional| Directional {
                    note: map(note.note),
                    tap: note.tap.map(tap),
                };
                match *note {
                    ScoreNote::Tap(note) => ScoreNote::Tap(tap(note)),
                    ScoreNote::Directional(note) => ScoreNote::Directional(directional(note)),
                    ScoreNote::Slide(note) => ScoreNote::Slide(Slide {
                        note: map(note.note),
                        tap: note.tap.map(tap),
                        directional: note.directional.map(directional),
                        ..note
                    }),
                }
            })
            .collect();
        let note_events = self
            .note_events
            .iter()
            .cloned()
            .map(|mut event| {
                event.bar = bar(event.bar);
                event
            })
            .collect();
        Self::from_parts(events, notes, note_events)
    }
}

pub(crate) fn merge_events(mut events: Vec<Event>) -> Vec<Event> {
    events.sort_by(|a, b| a.bar.total_cmp(&b.bar));
    let mut merged: Vec<Event> = Vec::new();
    for event in events {
        if let Some(last) = merged
            .last_mut()
            .filter(|last| format!("{:.6e}", last.bar) == format!("{:.6e}", event.bar))
        {
            last.merge(&event);
        } else {
            merged.push(event);
        }
    }
    merged
}

pub fn parse_notes(
    mut notes: Vec<ScoreNote>,
    add_slide_intervals: bool,
) -> (Vec<ScoreNote>, Vec<Event>) {
    notes.sort_by(|a, b| a.note().bar.total_cmp(&b.note().bar));
    let key = |note: Note| (note.bar.to_bits(), note.lane, note.width);
    let mut seen = HashSet::new();
    let mut events = Vec::new();
    notes.retain(|entry| {
        let note = entry.note();
        let track = match entry {
            ScoreNote::Tap(_) => 0,
            ScoreNote::Directional(_) => 1,
            ScoreNote::Slide(slide) => 2 + slide.channel,
        };
        if !seen.insert((key(note), note.kind, track)) {
            return false;
        }
        if (2..14).contains(&note.lane) {
            return true;
        }
        events.push(Event {
            bar: note.bar,
            text: Some(
                match (note.lane, note.kind) {
                    (0, _) => "SKILL",
                    (_, 1) => "FEVER CHANCE!",
                    _ => "SUPER FEVER!!",
                }
                .into(),
            ),
            ..Event::default()
        });
        false
    });
    let mut taps: HashMap<_, std::collections::VecDeque<_>> = HashMap::new();
    for (index, entry) in notes.iter().enumerate() {
        if let ScoreNote::Tap(Tap(note)) = entry {
            taps.entry(key(*note))
                .or_default()
                .push_back((index, Tap(*note)));
        }
    }
    let mut consumed = HashSet::new();
    let mut take_tap = |note| {
        let (index, tap) = taps.get_mut(&key(note))?.pop_front()?;
        consumed.insert(index);
        Some(tap)
    };
    let mut directions: HashMap<_, std::collections::VecDeque<_>> = HashMap::new();
    for (index, entry) in notes.iter_mut().enumerate() {
        if let ScoreNote::Directional(direction) = entry {
            direction.tap = take_tap(direction.note).or(direction.tap);
            directions
                .entry(key(direction.note))
                .or_default()
                .push_back((index, *direction));
        }
    }
    let mut used_directions = HashSet::new();
    for entry in &mut notes {
        if let ScoreNote::Slide(slide) = entry {
            slide.tap = take_tap(slide.note).or(slide.tap);
            if let Some((index, direction)) = directions
                .get_mut(&key(slide.note))
                .and_then(|queue| queue.pop_front())
            {
                used_directions.insert(index);
                slide.directional = Some(direction);
                slide.tap = direction.tap.or(slide.tap);
            }
        }
    }
    let mut notes: Vec<_> = notes
        .into_iter()
        .enumerate()
        .filter_map(|(index, note)| {
            (!consumed.contains(&index) && !used_directions.contains(&index)).then_some(note)
        })
        .collect();
    let mut channels = HashMap::<u8, (usize, usize)>::new();
    for index in 0..notes.len() {
        let ScoreNote::Slide(mut slide) = notes[index] else {
            continue;
        };
        let mut head = index;
        if let Some((mut previous, first)) = channels.remove(&slide.channel) {
            head = first;
            let ScoreNote::Slide(start) = notes[previous] else {
                unreachable!()
            };
            let mut bar = start.note.bar + 0.125;
            while add_slide_intervals && bar + 1e-3 < slide.note.bar {
                let interval = notes.len();
                link(&mut notes[previous], interval);
                notes.push(ScoreNote::Slide(Slide {
                    note: Note {
                        bar,
                        kind: 0,
                        ..start.note
                    },
                    channel: slide.channel,
                    tap: None,
                    directional: None,
                    head: Some(head),
                    next: None,
                }));
                previous = interval;
                bar += 0.125;
            }
            link(&mut notes[previous], index);
        }
        slide.head = Some(head);
        slide.next = None;
        notes[index] = ScoreNote::Slide(slide);
        if slide.note.kind != 2 {
            channels.insert(slide.channel, (index, head));
        }
    }
    let mut order: Vec<_> = (0..notes.len()).collect();
    order.sort_by(|&a, &b| notes[a].note().bar.total_cmp(&notes[b].note().bar));
    let mut inverse = vec![0; order.len()];
    for (new, &old) in order.iter().enumerate() {
        inverse[old] = new;
    }
    let notes = order
        .into_iter()
        .map(|old| match notes[old] {
            ScoreNote::Slide(slide) => ScoreNote::Slide(Slide {
                head: slide.head.map(|index| inverse[index]),
                next: slide.next.map(|index| inverse[index]),
                ..slide
            }),
            note => note,
        })
        .collect();
    (notes, events)
}

fn link(note: &mut ScoreNote, index: usize) {
    if let ScoreNote::Slide(slide) = note {
        slide.next = Some(index);
    }
}

fn digit(byte: u8) -> Result<u8> {
    (byte as char)
        .to_digit(36)
        .map(|value| value as u8)
        .with_context(|| format!("invalid SUS base-36 digit: {:?}", byte as char))
}

fn parse_data(data: &str) -> Result<impl Iterator<Item = (f64, &str)>> {
    ensure!(
        data.is_ascii() && data.len() % 2 == 0,
        "invalid SUS track data: {data}"
    );
    Ok((0..data.len()).step_by(2).filter_map(move |index| {
        let pair = &data[index..index + 2];
        (pair != "00").then_some((index as f64 / data.len() as f64, pair))
    }))
}

pub fn get_denominator(value: f64) -> u32 {
    let mut best = 1000;
    let mut remainder = 1000.;
    for denominator in [
        1, 2, 4, 8, 16, 32, 64, 128, 256, 3, 6, 12, 24, 48, 96, 192, 5, 10, 20, 40, 80, 160, 7, 14,
        28, 56, 112, 9, 18, 36, 72, 144, 11, 22, 33, 44, 36, 60, 84, 108, 120, 13, 26, 39, 52, 15,
        30, 45, 17, 19, 23, 25,
    ] {
        let fraction = (value * f64::from(denominator)).rem_euclid(1.);
        let error = fraction.min(1. - fraction);
        if error < remainder || (error < remainder + 1e-3 && denominator < best) {
            best = denominator;
            remainder = error;
        }
    }
    best
}

package helper

import (
	"sync"
	"time"
)

const (
	// Wrong passwords allowed before each try has to wait
	freeTries = 5
	// A quiet spell this long forgets earlier failures
	forgetAfter = 15 * time.Minute
	longestWait = 5 * time.Minute
)

type record struct {
	count int
	last  time.Time
}

// limiter slows down guessing: after freeTries failures on an account or
// from an address, each try waits twice as long as the one before.
type limiter struct {
	mu       sync.Mutex
	failures map[string]*record
}

func newLimiter() *limiter {
	return &limiter{failures: map[string]*record{}}
}

func (l *limiter) current(key string, now time.Time) *record {
	found := l.failures[key]
	if found != nil && now.Sub(found.last) > forgetAfter {
		delete(l.failures, key)
		return nil
	}
	return found
}

// wait says how long any of keys must still wait before the next try
func (l *limiter) wait(keys []string, now time.Time) time.Duration {
	l.mu.Lock()
	defer l.mu.Unlock()
	var longest time.Duration
	for _, key := range keys {
		found := l.current(key, now)
		if found == nil || found.count < freeTries {
			continue
		}
		delay := time.Second << min(found.count-freeTries, 9)
		delay = min(delay, longestWait)
		if remaining := found.last.Add(delay).Sub(now); remaining > longest {
			longest = remaining
		}
	}
	return longest
}

// fail records a failure for every key, returning the first key's new count
func (l *limiter) fail(keys []string, now time.Time) int {
	l.mu.Lock()
	defer l.mu.Unlock()
	first := 0
	for i, key := range keys {
		found := l.current(key, now)
		if found == nil {
			found = &record{}
			l.failures[key] = found
		}
		found.count++
		found.last = now
		if i == 0 {
			first = found.count
		}
	}
	// Forget stale entries so a scan of many names can't grow this forever
	if len(l.failures) > 10000 {
		for key := range l.failures {
			l.current(key, now)
		}
	}
	return first
}

func (l *limiter) succeed(keys []string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for _, key := range keys {
		delete(l.failures, key)
	}
}

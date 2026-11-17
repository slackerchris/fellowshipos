package helper

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

// CPUTicks is one reading of /proc/stat's first line. iowait counts as idle:
// waiting on a disk is not processor work. steal is kept apart, because in a VM
// it is time the host took back, not work this machine did.
type CPUTicks struct {
	Total uint64
	Idle  uint64
	Steal uint64
}

func ReadCPUTicks() (CPUTicks, error) {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return CPUTicks{}, err
	}
	fields := strings.Fields(strings.SplitN(string(data), "\n", 2)[0])[1:]
	var ticks CPUTicks
	for i, field := range fields {
		n, err := strconv.ParseUint(field, 10, 64)
		if err != nil {
			return CPUTicks{}, err
		}
		ticks.Total += n
		if i == 3 || i == 4 {
			ticks.Idle += n
		}
		if i == 7 {
			ticks.Steal = n
		}
	}
	return ticks, nil
}

// CPUPercent is the busy and steal share of the ticks between two readings.
func CPUPercent(before, after CPUTicks) (busy, steal float64) {
	elapsed := after.Total - before.Total
	if elapsed == 0 {
		return 0, 0
	}
	return float64(elapsed-(after.Idle-before.Idle)) * 100 / float64(elapsed),
		float64(after.Steal-before.Steal) * 100 / float64(elapsed)
}

type CPUSample struct {
	Time  int64   `json:"time"`
	Busy  float64 `json:"busy"`
	Steal float64 `json:"steal"`
}

// CPUSampler watches /proc/stat continuously so no request ever measures the
// act of measuring: a tenth-of-a-second sample inside a fresh process mostly
// sees that process starting. It keeps five minutes of samples and a smoothed
// now, and writes them where fnas health reads them, so the Health page, the
// desktop widget and the command line all show one number.
type CPUSampler struct {
	mu      sync.Mutex
	samples []CPUSample
}

func (s *CPUSampler) add(now time.Time, busy, steal float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.samples = append(s.samples, CPUSample{Time: now.Unix(), Busy: busy, Steal: steal})
	cutoff := now.Add(-5 * time.Minute).Unix()
	for len(s.samples) > 0 && s.samples[0].Time < cutoff {
		s.samples = s.samples[1:]
	}
}

// Now is the mean of the last ten seconds, so one busy moment doesn't jump the
// dial and one quiet one doesn't drop it to zero.
func (s *CPUSampler) Now(now time.Time) (busy, steal float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	cutoff := now.Add(-10 * time.Second).Unix()
	var sum, stealSum float64
	var n int
	for _, sample := range s.samples {
		if sample.Time >= cutoff {
			sum += sample.Busy
			stealSum += sample.Steal
			n++
		}
	}
	if n == 0 {
		return 0, 0
	}
	return sum / float64(n), stealSum / float64(n)
}

func (s *CPUSampler) History() []CPUSample {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]CPUSample(nil), s.samples...)
}

// Run samples every interval and writes the smoothed figure and the history.
func (s *CPUSampler) Run(interval time.Duration, path string) {
	before, err := ReadCPUTicks()
	if err != nil {
		return
	}
	for {
		time.Sleep(interval)
		after, err := ReadCPUTicks()
		if err != nil {
			return
		}
		busy, steal := CPUPercent(before, after)
		before = after
		now := time.Now()
		s.add(now, busy, steal)
		smooth, smoothSteal := s.Now(now)
		data, err := json.Marshal(struct {
			Time    int64       `json:"time"`
			Busy    float64     `json:"busy"`
			Steal   float64     `json:"steal"`
			History []CPUSample `json:"history"`
		}{now.Unix(), smooth, smoothSteal, s.History()})
		if err == nil {
			os.WriteFile(path, data, 0o644)
		}
	}
}

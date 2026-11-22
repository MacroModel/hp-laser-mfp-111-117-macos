// SPDX-License-Identifier: AGPL-3.0-or-later
// Local PDF printer. The OS retains the application's CUPS job and options.
// The worker runs in the signed-in user's GUI session, one entire job at a time.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	ipp "github.com/OpenPrinting/goipp"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

var port = flag.Int("port", 18615, "loopback IPP port")
var spool = flag.String("spool", "", "private spool directory")
var worker = flag.String("worker", "", "print processor")
var mediaFile = flag.String("media", "", "JSON array of PWG paper names")
var media []string
var uri string
var started = time.Now()
var operations = []ipp.Op{ipp.OpPrintJob, ipp.OpValidateJob, ipp.OpCreateJob, ipp.OpSendDocument, ipp.OpCancelJob, ipp.OpGetJobAttributes, ipp.OpGetJobs, ipp.OpGetPrinterAttributes}

type job struct {
	ID        int
	State     int
	Reason    string
	Options   map[string]string
	file      string
	cancel    context.CancelFunc
	receiving bool
	updated   time.Time
}
type store struct {
	sync.Mutex
	jobs  map[int]*job
	next  int
	ready chan int
}

var q = store{jobs: make(map[int]*job), ready: make(chan int, 64)}

func str(k string, tag ipp.Tag, values ...string) ipp.Attribute {
	a := ipp.MakeAttribute(k, tag, ipp.String(values[0]))
	for _, v := range values[1:] {
		a.Values.Add(tag, ipp.String(v))
	}
	return a
}
func integer(k string, tag ipp.Tag, nums ...int) ipp.Attribute {
	a := ipp.MakeAttribute(k, tag, ipp.Integer(nums[0]))
	for _, v := range nums[1:] {
		a.Values.Add(tag, ipp.Integer(v))
	}
	return a
}
func first(attrs ipp.Attributes, k string) string {
	for _, a := range attrs {
		if a.Name == k && len(a.Values) > 0 {
			return a.Values[0].V.String()
		}
	}
	return ""
}
func capability() ipp.Attributes {
	var ops []int
	for _, op := range operations {
		ops = append(ops, int(op))
	}
	r1 := ipp.Resolution{Xres: 600, Yres: 600, Units: ipp.UnitsDpi}
	r2 := ipp.Resolution{Xres: 1200, Yres: 1200, Units: ipp.UnitsDpi}
	busy, pending := 3, 0
	for _, j := range q.jobs {
		if j.State == 5 {
			busy = 4
		}
		if j.State < 7 {
			pending++
		}
	}
	return ipp.Attributes{
		ipp.MakeAttrCollection("media-col-default", ipp.MakeAttrCollection("media-size", integer("x-dimension", ipp.TagInteger, 21000), integer("y-dimension", ipp.TagInteger, 29700))),
		str("printer-location", ipp.TagText, "This Mac"), str("printer-more-info", ipp.TagURI, "https://support.hp.com/cn-zh/product/setup-user-guides/hp-laser-mfp-100-printer-series/model/2102352114"),
		str("printer-uri-supported", ipp.TagURI, uri), str("printer-name", ipp.TagName, "HP115nw SPL3"), str("printer-info", ipp.TagText, "HP Laser MFP 111-117"), str("printer-make-and-model", ipp.TagText, "HP Laser MFP 115nw SPL3 for Mac"),
		str("uri-security-supported", ipp.TagKeyword, "none"), str("uri-authentication-supported", ipp.TagKeyword, "none"),
		str("ipp-versions-supported", ipp.TagKeyword, "1.1", "2.0"), integer("operations-supported", ipp.TagEnum, ops...),
		str("charset-configured", ipp.TagCharset, "utf-8"), str("charset-supported", ipp.TagCharset, "utf-8", "us-ascii"),
		str("natural-language-configured", ipp.TagLanguage, "en"), str("generated-natural-language-supported", ipp.TagLanguage, "en", "zh-cn"),
		str("document-format-default", ipp.TagMimeType, "application/pdf"), str("document-format-supported", ipp.TagMimeType, "application/pdf", "application/octet-stream"),
		str("compression-supported", ipp.TagKeyword, "none"), str("pdl-override-supported", ipp.TagKeyword, "not-attempted"),
		integer("printer-state", ipp.TagEnum, busy), str("printer-state-reasons", ipp.TagKeyword, "none"), integer("queued-job-count", ipp.TagInteger, pending),
		ipp.MakeAttribute("printer-is-accepting-jobs", ipp.TagBoolean, ipp.Boolean(true)), ipp.MakeAttribute("color-supported", ipp.TagBoolean, ipp.Boolean(false)),
		integer("printer-up-time", ipp.TagInteger, int(time.Since(started).Seconds())+1), integer("printer-state-change-time", ipp.TagInteger, 1),
		str("printer-uuid", ipp.TagURI, "urn:uuid:e04db507-7242-470a-a337-462014b23073"),
		ipp.MakeAttribute("printer-resolution-default", ipp.TagResolution, r1), ipp.MakeAttr("printer-resolution-supported", ipp.TagResolution, r1, r2),
		integer("print-quality-default", ipp.TagEnum, 4), integer("print-quality-supported", ipp.TagEnum, 4, 5), integer("copies-default", ipp.TagInteger, 1),
		ipp.MakeAttribute("copies-supported", ipp.TagRange, ipp.Range{Lower: 1, Upper: 999}),
		str("sides-default", ipp.TagKeyword, "one-sided"), str("sides-supported", ipp.TagKeyword, "one-sided", "two-sided-long-edge", "two-sided-short-edge"),
		str("media-default", ipp.TagKeyword, media[0]), str("media-supported", ipp.TagKeyword, media...),
		str("media-type-supported", ipp.TagKeyword, "auto", "stationery", "stationery-heavyweight", "stationery-lightweight", "stationery-bond", "stationery-colored", "cardstock", "labels", "envelope", "stationery-preprinted", "stationery-recycled"),
		str("media-col-supported", ipp.TagKeyword, "media-size", "media-type", "media-source"), str("media-source-supported", ipp.TagKeyword, "auto", "main"),
		str("multiple-document-handling-supported", ipp.TagKeyword, "separate-documents-collated-copies", "separate-documents-uncollated-copies"),
		ipp.MakeAttribute("multiple-document-jobs-supported", ipp.TagBoolean, ipp.Boolean(false)),
		str("job-creation-attributes-supported", ipp.TagKeyword, "copies", "sides", "media", "media-col", "media-type", "media-source", "printer-resolution", "print-quality", "multiple-document-handling"),
	}
}
func pathFor(id int) string { return filepath.Join(*spool, strconv.Itoa(id)+".json") }
func persist(j *job) {
	j.updated = time.Now()
	data, err := json.Marshal(j)
	if err != nil {
		return
	}
	p := pathFor(j.ID)
	if os.WriteFile(p+".new", data, 0600) == nil {
		_ = os.Rename(p+".new", p)
	}
}
func finish(j *job, state int, msg string) {
	j.State = state
	j.Reason = msg
	persist(j)
	if j.file != "" {
		_ = os.Remove(j.file)
	}
}
func attrs(j *job) ipp.Attributes {
	return ipp.Attributes{integer("job-id", ipp.TagInteger, j.ID), str("job-uri", ipp.TagURI, uri+"/jobs/"+strconv.Itoa(j.ID)), str("job-printer-uri", ipp.TagURI, uri), integer("job-state", ipp.TagEnum, j.State), str("job-state-reasons", ipp.TagKeyword, j.Reason), str("job-name", ipp.TagName, j.Options["job-name"])}
}
func saveOpts(j *job, m *ipp.Message) {
	for _, group := range []ipp.Attributes{m.Operation, m.Job} {
		for _, a := range group {
			switch a.Name {
			case "job-name", "requesting-user-name", "copies", "sides", "HPDuplexMode", "media", "media-type", "media-source", "print-quality", "printer-resolution", "multiple-document-handling", "page-ranges", "number-up", "orientation-requested":
				j.Options[a.Name] = a.Values.String()
			case "media-col":
				if len(a.Values) > 0 {
					if col, ok := a.Values[0].V.(ipp.Collection); ok {
						for _, m := range col {
							if len(m.Values) == 0 {
								continue
							}
							if m.Name == "media-size" {
								if size, ok := m.Values[0].V.(ipp.Collection); ok {
									for _, d := range size {
										j.Options[d.Name] = d.Values[0].V.String()
									}
								}
							} else if m.Name == "media-type" || m.Name == "media-source" {
								j.Options[m.Name] = m.Values[0].V.String()
							}
						}
					}
				}
			}
		}
	}
	if j.Options["job-name"] == "" {
		j.Options["job-name"] = "Document"
	}
}
func spoolPDF(j *job, r *bufio.Reader) error {
	prefix, err := r.Peek(5)
	if err != nil || string(prefix) != "%PDF-" {
		return errors.New("expected PDF")
	}
	path := filepath.Join(*spool, strconv.Itoa(j.ID)+".pdf")
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	_, err = io.Copy(f, r)
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		os.Remove(path)
		return err
	}
	q.Lock()
	defer q.Unlock()
	if j.State == 7 {
		os.Remove(path)
		return errors.New("job cancelled")
	}
	j.file = path
	j.State = 3
	j.Reason = "job-queued"
	persist(j)
	select {
	case q.ready <- j.ID:
		return nil
	default:
		return errors.New("print queue is busy")
	}
}
func runJobs() {
	for id := range q.ready {
		q.Lock()
		j := q.jobs[id]
		if j == nil || j.State != 3 {
			q.Unlock()
			continue
		}
		ctx, cancel := context.WithCancel(context.Background())
		j.cancel = cancel
		j.State = 5
		j.Reason = "job-printing"
		persist(j)
		q.Unlock()
		cmd := exec.CommandContext(ctx, *worker, "process", j.file, pathFor(id))
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		cmd.Cancel = func() error {
			if cmd.Process == nil {
				return os.ErrProcessDone
			}
			return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
		}
		cmd.WaitDelay = 3 * time.Second
		cmd.Stderr = os.Stderr
		err := cmd.Run()
		cancel()
		q.Lock()
		if j.State != 7 {
			if err == nil {
				finish(j, 9, "job-completed-successfully")
			} else if e, ok := err.(*exec.ExitError); ok && e.ExitCode() == 2 {
				finish(j, 7, "job-canceled-by-user")
			} else {
				finish(j, 8, "aborted-by-system")
			}
		}
		j.cancel = nil
		q.Unlock()
	}
}
func handler(w http.ResponseWriter, r *http.Request) {
	host, _, _ := net.SplitHostPort(r.Host)
	if host != "127.0.0.1" && host != "localhost" {
		http.Error(w, "Local printer only", 403)
		return
	}
	if r.Method != "POST" || r.Header.Get("Content-Type") != "application/ipp" || (r.URL.Path != "/ipp/print" && !strings.HasPrefix(r.URL.Path, "/ipp/print/jobs/")) {
		http.NotFound(w, r)
		return
	}
	// A buffered reader retains PDF bytes read along with the IPP attribute section.
	body := bufio.NewReader(http.MaxBytesReader(w, r.Body, 1<<30))
	var m ipp.Message
	if err := m.Decode(io.LimitReader(body, 1<<20)); err != nil {
		http.Error(w, "Invalid IPP request", 400)
		return
	}
	resp := ipp.NewResponse(m.Version, ipp.StatusOk, m.RequestID)
	resp.Operation = ipp.Attributes{str("attributes-charset", ipp.TagCharset, "utf-8"), str("attributes-natural-language", ipp.TagLanguage, "en")}
	status := func(n int) { resp.Code = ipp.Code(n) }
	q.Lock()
	id, _ := strconv.Atoi(first(m.Operation, "job-id"))
	if id == 0 {
		u := strings.TrimRight(first(m.Operation, "job-uri"), "/")
		if at := strings.LastIndex(u, "/"); at >= 0 {
			id, _ = strconv.Atoi(u[at+1:])
		}
	}
	j := q.jobs[id]
	op := ipp.Op(m.Code)
	switch op {
	case ipp.OpGetPrinterAttributes:
		resp.Printer = capability()
	case ipp.OpGetJobs:
		which := first(m.Operation, "which-jobs")
		ids := []int{}
		for id, j := range q.jobs {
			if which == "all" || (j.State >= 7) == (which == "completed") {
				ids = append(ids, id)
			}
		}
		sort.Ints(ids)
		resp.Groups = ipp.Groups{{Tag: ipp.TagOperationGroup, Attrs: resp.Operation}}
		for _, id := range ids {
			resp.Groups = append(resp.Groups, ipp.Group{Tag: ipp.TagJobGroup, Attrs: attrs(q.jobs[id])})
		}
	case ipp.OpGetJobAttributes:
		if j == nil {
			status(0x406)
		} else {
			resp.Job = attrs(j)
		}
	case ipp.OpCancelJob:
		if j == nil {
			status(0x406)
		} else if j.State >= 7 {
			status(0x404)
		} else {
			if j.cancel != nil {
				j.cancel()
			}
			finish(j, 7, "job-canceled-by-user")
		}
	case ipp.OpValidateJob, ipp.OpCreateJob, ipp.OpPrintJob, ipp.OpSendDocument:
		format := first(m.Operation, "document-format")
		cx := first(m.Operation, "compression")
		if (format != "" && format != "application/pdf" && format != "application/octet-stream") || (cx != "" && cx != "none") {
			status(0x40a)
			break
		}
		if r := first(m.Job, "printer-resolution"); r != "" && r != "600dpi" && r != "1200dpi" && r != "600x600dpi" && r != "1200x1200dpi" {
			status(0x40b)
			break
		}
		if v := first(m.Job, "sides"); v != "" && v != "one-sided" && v != "two-sided-long-edge" && v != "two-sided-short-edge" {
			status(0x40b)
			break
		}
		if v := first(m.Job, "copies"); v != "" {
			n, e := strconv.Atoi(v)
			if e != nil || n < 1 || n > 999 {
				status(0x40b)
				break
			}
		}
		if op == ipp.OpValidateJob {
			break
		}
		if op != ipp.OpSendDocument {
			active := 0
			for _, entry := range q.jobs {
				if entry.State < 7 {
					active++
				}
			}
			if active >= 32 || len(q.ready) >= cap(q.ready) {
				status(0x507)
				break
			}
			q.next++
			j = &job{ID: q.next, State: 4, Reason: "job-incoming", Options: map[string]string{}}
			q.jobs[j.ID] = j
		} else if j == nil {
			status(0x406)
			break
		} else if j.State != 4 || j.receiving || first(m.Operation, "last-document") == "false" {
			status(0x404)
			break
		}
		saveOpts(j, &m)
		// CUPS may consume a vendor PPD option without including its IPP equivalent.
		// Capture only a matching processing job, while the Apple backend is waiting for our reply.
		{
			for k, v := range captureTicket(j.Options["job-name"], j.Options["requesting-user-name"]) {
				j.Options[k] = v
			}
		}
		persist(j)
		if op != ipp.OpCreateJob {
			j.receiving = true
			q.Unlock()
			err := spoolPDF(j, body)
			q.Lock()
			j.receiving = false
			if err != nil {
				if j.State != 7 {
					finish(j, 8, "document-format-error")
				}
				status(0x40a)
				break
			}
		}
		resp.Job = attrs(j)
	default:
		status(0x501)
	}
	q.Unlock()
	var data bytes.Buffer
	if err := resp.Encode(&data); err != nil {
		http.Error(w, "IPP response failed", 500)
		return
	}
	w.Header().Set("Content-Type", "application/ipp")
	w.Write(data.Bytes())
}
func prune() {
	q.Lock()
	defer q.Unlock()
	for id, j := range q.jobs {
		if j.State == 4 && !j.receiving && time.Since(j.updated) > 10*time.Minute {
			finish(j, 8, "job-data-insufficient")
		}
		if j.State >= 7 && time.Since(j.updated) > 24*time.Hour {
			delete(q.jobs, id)
			os.Remove(pathFor(id))
		}
	}
}
func main() {
	flag.Parse()
	if *spool == "" || *worker == "" {
		log.Fatal("Missing printer configuration")
	}
	data, err := os.ReadFile(*mediaFile)
	if err != nil || json.Unmarshal(data, &media) != nil || len(media) == 0 {
		log.Fatal("Invalid media list")
	}
	if err = os.MkdirAll(*spool, 0700); err != nil {
		log.Fatal(err)
	}
	q.next = int(time.Now().Unix()) % 100000000
	files, _ := filepath.Glob(filepath.Join(*spool, "*.json"))
	for _, p := range files {
		var j job
		b, e := os.ReadFile(p)
		if e == nil && json.Unmarshal(b, &j) == nil && j.ID > 0 {
			if j.ID > q.next {
				q.next = j.ID
			}
			j.file = filepath.Join(*spool, strconv.Itoa(j.ID)+".pdf")
			q.jobs[j.ID] = &j
			if j.State < 7 {
				finish(&j, 8, "aborted-by-system")
			} else if info, e := os.Stat(p); e == nil {
				j.updated = info.ModTime()
			}
		}
	}
	go runJobs()
	go func() {
		for range time.Tick(time.Minute) {
			prune()
		}
	}()
	addr := fmt.Sprintf("127.0.0.1:%d", *port)
	uri = "ipp://" + addr + "/ipp/print"
	server := http.Server{Addr: addr, Handler: http.HandlerFunc(handler), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Minute, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16384}
	log.Fatal(server.ListenAndServe())
}

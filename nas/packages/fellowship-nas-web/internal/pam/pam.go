// Package pam checks a password against the system's accounts through
// libpam, so the desktop accepts exactly the passwords SSH accepts.
package pam

/*
#cgo LDFLAGS: -lpam
#include <security/pam_appl.h>
#include <stdlib.h>
#include <string.h>

// Answers PAM's password prompt with the password passed as data. Any other
// prompt fails, since the user name is already set.
static int converse(int count, const struct pam_message **messages,
                    struct pam_response **responses, void *data) {
    struct pam_response *answers = calloc(count, sizeof(struct pam_response));
    if (answers == NULL) {
        return PAM_BUF_ERR;
    }
    for (int i = 0; i < count; i++) {
        switch (messages[i]->msg_style) {
        case PAM_PROMPT_ECHO_OFF:
            answers[i].resp = strdup((const char *)data);
            if (answers[i].resp == NULL) {
                goto fail;
            }
            break;
        case PAM_ERROR_MSG:
        case PAM_TEXT_INFO:
            break;
        default:
            goto fail;
        }
    }
    *responses = answers;
    return PAM_SUCCESS;
fail:
    for (int i = 0; i < count; i++) {
        if (answers[i].resp != NULL) {
            explicit_bzero(answers[i].resp, strlen(answers[i].resp));
            free(answers[i].resp);
        }
    }
    free(answers);
    return PAM_CONV_ERR;
}

static int check_password(const char *service, const char *user, const char *password) {
    struct pam_conv conversation = { converse, (void *)password };
    pam_handle_t *handle = NULL;
    int status = pam_start(service, user, &conversation, &handle);
    if (status != PAM_SUCCESS) {
        return status;
    }
    status = pam_authenticate(handle, PAM_DISALLOW_NULL_AUTHTOK);
    if (status == PAM_SUCCESS) {
        status = pam_acct_mgmt(handle, PAM_DISALLOW_NULL_AUTHTOK);
    }
    pam_end(handle, status);
    return status;
}

static void forget(char *text) {
    explicit_bzero(text, strlen(text));
    free(text);
}
*/
import "C"

import (
	"errors"
	"fmt"
	"unsafe"
)

// ErrExpired means the password was right but must be changed before use
var ErrExpired = errors.New("password expired")

// Authenticate checks user's password with the PAM service, and that the
// account may log in now.
func Authenticate(service, user, password string) error {
	cService, cUser, cPassword := C.CString(service), C.CString(user), C.CString(password)
	defer C.free(unsafe.Pointer(cService))
	defer C.free(unsafe.Pointer(cUser))
	defer C.forget(cPassword)
	switch status := C.check_password(cService, cUser, cPassword); status {
	case C.PAM_SUCCESS:
		return nil
	case C.PAM_NEW_AUTHTOK_REQD:
		return ErrExpired
	default:
		return fmt.Errorf("pam: %s", C.GoString(C.pam_strerror(nil, status)))
	}
}

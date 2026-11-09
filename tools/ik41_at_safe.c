/* Minimal IK41 USB AT reader and mode switcher.
 * Only interface 2 is claimed; RNDIS/MBIM data interfaces are untouched.
 * No SIM identifier, factory reset, APN write, or firmware command exists.
 */
#include <libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void fail(const char *where, int code) {
    fprintf(stderr, "%s: %s\n", where, libusb_error_name(code));
    exit(1);
}

static const char *command_for(const char *action) {
    if (!strcmp(action, "mode")) return "AT+USBMODE?\r";
    if (!strcmp(action, "apn")) return "AT+CGDCONT?\r";
    if (!strcmp(action, "set-mbim")) return "AT+USBMODE=0\r";
    if (!strcmp(action, "set-rndis")) return "AT+USBMODE=1\r";
    return NULL;
}

static int report_apn(char *response) {
    char *line = response;
    while ((line = strstr(line, "+CGDCONT:")) != NULL) {
        int cid = -1;
        if (sscanf(line, "+CGDCONT: %d", &cid) == 1 && cid == 1) {
            char *first = strchr(line, '"');
            char *type_end = first ? strchr(first + 1, '"') : NULL;
            char *apn_start = type_end ? strchr(type_end + 1, '"') : NULL;
            char *apn_end = apn_start ? strchr(apn_start + 1, '"') : NULL;
            if (!first || !type_end || !apn_start || !apn_end ||
                apn_end - apn_start > 129) return 2;
            printf("Context 1 PDP type: %.*s\n", (int)(type_end - first - 1), first + 1);
            printf("Context 1 APN: %.*s\n", (int)(apn_end - apn_start - 1), apn_start + 1);
            return 0;
        }
        line++;
    }
    fprintf(stderr, "Context 1 APN was not reported\n");
    return 2;
}

static int report_mode(char *response) {
    char *mode = strstr(response, "+USBMODE:");
    if (!mode) {
        fprintf(stderr, "USB mode was not reported\n");
        return 2;
    }
    int value = -1;
    if (sscanf(mode, "+USBMODE: %d", &value) != 1 || value < 0 || value > 2) {
        fprintf(stderr, "USB mode response was not recognized\n");
        return 2;
    }
    printf("USB mode: %d\n", value);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 || !command_for(argv[1])) {
        fprintf(stderr, "usage: %s mode|apn|set-mbim|set-rndis\n", argv[0]);
        return 2;
    }
    libusb_context *ctx = NULL;
    libusb_device **devices = NULL;
    libusb_device_handle *handle = NULL;
    struct libusb_config_descriptor *config = NULL;
    int rc = libusb_init(&ctx);
    if (rc) fail("libusb_init", rc);
    ssize_t count = libusb_get_device_list(ctx, &devices);
    if (count < 0) fail("libusb_get_device_list", (int)count);
    int result = 1;
    for (ssize_t i = 0; i < count; i++) {
        struct libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(devices[i], &desc)) continue;
        if (desc.idVendor != 0x1bbb ||
            (desc.idProduct != 0x01aa && desc.idProduct != 0x00b6)) continue;
        rc = libusb_get_active_config_descriptor(devices[i], &config);
        if (rc) fail("active USB configuration", rc);
        if (config->bNumInterfaces <= 2) {
            fprintf(stderr, "AT interface 2 is absent\n");
            break;
        }
        unsigned char in = 0, out = 0;
        const struct libusb_interface *iface = &config->interface[2];
        for (int a = 0; a < iface->num_altsetting; a++) {
            const struct libusb_interface_descriptor *alt = &iface->altsetting[a];
            if (alt->bInterfaceNumber != 2 || alt->bAlternateSetting != 0) continue;
            for (int e = 0; e < alt->bNumEndpoints; e++) {
                const struct libusb_endpoint_descriptor *ep = &alt->endpoint[e];
                if ((ep->bmAttributes & LIBUSB_TRANSFER_TYPE_MASK) !=
                    LIBUSB_TRANSFER_TYPE_BULK) continue;
                if (ep->bEndpointAddress & LIBUSB_ENDPOINT_IN) in = ep->bEndpointAddress;
                else out = ep->bEndpointAddress;
            }
        }
        if (!in || !out) {
            fprintf(stderr, "AT bulk endpoints are absent\n");
            break;
        }
        rc = libusb_open(devices[i], &handle);
        if (rc) fail("open modem", rc);
        rc = libusb_claim_interface(handle, 2);
        if (rc) fail("claim AT interface", rc);
        const char *command = command_for(argv[1]);
        int transferred = 0;
        rc = libusb_bulk_transfer(handle, out, (unsigned char *)command,
                                  (int)strlen(command), &transferred, 3000);
        if (rc) fail("send AT command", rc);
        char response[4096] = {0};
        size_t length = 0;
        for (int attempt = 0; attempt < 4 && length < sizeof(response) - 1; attempt++) {
            rc = libusb_bulk_transfer(handle, in, (unsigned char *)response + length,
                                      (int)(sizeof(response) - length - 1),
                                      &transferred, 3000);
            if (rc) break;
            length += (size_t)transferred;
            response[length] = 0;
            if (strstr(response, "\r\nOK\r\n") || strstr(response, "\r\nERROR\r\n")) break;
        }
        if (!strcmp(argv[1], "apn")) result = report_apn(response);
        else if (!strcmp(argv[1], "mode")) result = report_mode(response);
        else {
            printf("Mode change command sent; verify the new USB ID before continuing.\n");
            result = 0;
        }
        libusb_release_interface(handle, 2);
        libusb_close(handle);
        break;
    }
    if (!config) fprintf(stderr, "Supported IK41 USB ID not found\n");
    libusb_free_config_descriptor(config);
    libusb_free_device_list(devices, 1);
    libusb_exit(ctx);
    return result;
}

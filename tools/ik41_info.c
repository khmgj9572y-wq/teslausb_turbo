/* Read-only IK41 telemetry. Claims only vendor AT interface 2.
 * No reset, network scan, mode setter, APN setter, or firmware command.
 */
#define _POSIX_C_SOURCE 200809L
#include <libusb.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <time.h>
#include <unistd.h>

struct query { const char *name, *command, *group; };
static const struct query queries[] = {
    {"rssi", "AT+CSQ\r", "live"},
    {"extended_signal", "AT+CESQ\r", "live"},
    {"lte_signal", "AT^LTERSRP?\r", "live"},
    {"radio_signal", "AT^HCSQ?\r", "live"},
    {"band", "AT^GETBAND?\r", "live"},
    {"system", "AT^SYSINFOEX\r", "live"},
    {"operator", "AT+COPS?\r", "live"},
    {"registration", "AT+CEREG?\r", "live"},
    {"packet_registration", "AT+CGREG?\r", "live"},
    {"circuit_registration", "AT+CREG?\r", "live"},
    {"attachment", "AT+CGATT?\r", "live"},
    {"sim", "AT+CPIN?\r", "live"},
    {"contexts_active", "AT+CGACT?\r", "live"},
    {"context_details", "AT+CGCONTRDP\r", "live"},
    {"pdp_addresses", "AT+CGPADDR\r", "live"},
    {"chain_rsrp", "AT$QCRSRP?\r", "live"},
    {"chain_rsrq", "AT$QCRSRQ?\r", "live"},
    {"manufacturer", "AT+GMI\r", "device"},
    {"model", "AT+GMM\r", "device"},
    {"firmware", "AT+GMR\r", "device"},
    {"usb_mode", "AT+USBMODE?\r", "device"},
    {"apn", "AT+CGDCONT?\r", "device"},
    {"functionality", "AT+CFUN?\r", "device"},
    {"capabilities", "AT+GCAP\r", "device"},
    {"last_error", "AT+CEER\r", "device"},
    {"imei", "AT+CGSN\r", "identity"},
    {"imsi", "AT+CIMI\r", "identity"},
    {"iccid", "AT+CCID\r", "identity"},
    {"iccid_file", "AT+CRSM=176,12258,0,0,10\r", "identity"},
    {"phone", "AT+CNUM\r", "identity"},
    {"commands", "AT+CLAC\r", "supported"},
};

static double seconds(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static void string(const char *s) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p < 32 || *p >= 127) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static int error(const char *s) {
    printf("{\"ok\":false,\"error\":"); string(s);
    printf(",\"responses\":{}}\n");
    return 1;
}

static int query(libusb_device_handle *handle, unsigned char in, unsigned char out,
                 const char *command, char *response, size_t capacity) {
    int transferred = 0;
    int rc = libusb_bulk_transfer(handle, out, (unsigned char *)command,
                                  (int)strlen(command), &transferred, 2000);
    if (rc || transferred != (int)strlen(command)) return 0;
    size_t length = 0;
    double end = seconds() + 2.0;
    while (length < capacity - 1 && seconds() < end) {
        rc = libusb_bulk_transfer(handle, in, (unsigned char *)response + length,
                                  (int)(capacity - length - 1), &transferred, 200);
        if (rc == LIBUSB_ERROR_TIMEOUT) continue;
        if (rc || transferred < 0) break;
        length += (size_t)transferred;
        response[length] = 0;
        if (strstr(response, "\r\nOK\r\n")) return 1;
        if (strstr(response, "\r\nERROR\r\n") || strstr(response, "+CME ERROR:")) break;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "live") && strcmp(argv[1], "device") &&
                     strcmp(argv[1], "identity") && strcmp(argv[1], "supported")))
        return error("Choose live, device, identity, or supported");
    int lock = open("/run/lock/ik41-at.lock", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (lock < 0) return error("AT lock unavailable");
    double until = seconds() + 2.0;
    while (flock(lock, LOCK_EX | LOCK_NB)) {
        if (errno != EWOULDBLOCK || seconds() >= until) { close(lock); return error("AT interface busy"); }
        struct timespec delay = {0, 20000000}; nanosleep(&delay, NULL);
    }
    libusb_context *ctx = NULL;
    libusb_device **devices = NULL;
    libusb_device_handle *handle = NULL;
    struct libusb_config_descriptor *config = NULL;
    unsigned char in = 0, out = 0;
    if (libusb_init(&ctx)) { close(lock); return error("USB unavailable"); }
    ssize_t count = libusb_get_device_list(ctx, &devices);
    for (ssize_t i = 0; i < count && !handle; i++) {
        struct libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(devices[i], &desc) || desc.idVendor != 0x1bbb ||
                (desc.idProduct != 0x01aa && desc.idProduct != 0x00b6)) continue;
        in = 0; out = 0;
        if (libusb_get_active_config_descriptor(devices[i], &config)) continue;
        for (int j = 0; j < config->bNumInterfaces; j++) {
            const struct libusb_interface *iface = &config->interface[j];
            for (int a = 0; a < iface->num_altsetting; a++) {
                const struct libusb_interface_descriptor *alt = &iface->altsetting[a];
                if (alt->bInterfaceNumber != 2 || alt->bAlternateSetting || alt->bInterfaceClass != 0xff) continue;
                for (int e = 0; e < alt->bNumEndpoints; e++) {
                    const struct libusb_endpoint_descriptor *ep = &alt->endpoint[e];
                    if ((ep->bmAttributes & LIBUSB_TRANSFER_TYPE_MASK) != LIBUSB_TRANSFER_TYPE_BULK) continue;
                    if (ep->bEndpointAddress & LIBUSB_ENDPOINT_IN) in = ep->bEndpointAddress;
                    else out = ep->bEndpointAddress;
                }
            }
        }
        if (in && out && !libusb_open(devices[i], &handle)) {
            if (libusb_kernel_driver_active(handle, 2) == 1 || libusb_claim_interface(handle, 2)) {
                libusb_close(handle); handle = NULL;
            }
        }
        libusb_free_config_descriptor(config); config = NULL;
    }
    int result = 0;
    if (!handle) result = error("Modem AT interface unavailable");
    else {
        char stale[1024]; int received;
        for (int i = 0; i < 4; i++)
            if (libusb_bulk_transfer(handle, in, (unsigned char *)stale, sizeof(stale), &received, 20)) break;
        printf("{\"ok\":true,\"responses\":{");
        int first = 1;
        for (size_t i = 0; i < sizeof(queries) / sizeof(queries[0]); i++) {
            if (strcmp(argv[1], queries[i].group)) continue;
            char response[65536] = {0};
            int ok = query(handle, in, out, queries[i].command, response, sizeof(response));
            if (!first) putchar(',');
            first = 0;
            string(queries[i].name); printf(":{\"ok\":%s,\"text\":", ok ? "true" : "false");
            string(response); putchar('}');
        }
        printf("}}\n");
        libusb_release_interface(handle, 2); libusb_close(handle);
    }
    libusb_free_device_list(devices, 1); libusb_exit(ctx); close(lock);
    return result;
}

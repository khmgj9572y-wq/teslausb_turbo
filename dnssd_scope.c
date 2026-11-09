/* Scope only this process's DNS-SD registrations to its hotspot interface.
 * Debian UxPlay 1.71 links libdns_sd directly. Other Avahi publishers keep
 * their existing interface policy. Missing interfaces fail closed.
 */
#define _GNU_SOURCE
#include <dns_sd.h>
#include <dlfcn.h>
#include <net/if.h>
#include <stdlib.h>

typedef DNSServiceErrorType (*register_fn)(DNSServiceRef *, DNSServiceFlags,
    uint32_t, const char *, const char *, const char *, const char *, uint16_t,
    uint16_t, const void *, DNSServiceRegisterReply, void *);

DNSServiceErrorType DNSServiceRegister(DNSServiceRef *ref, DNSServiceFlags flags,
    uint32_t ignored_interface, const char *name, const char *type,
    const char *domain, const char *host, uint16_t port, uint16_t txt_length,
    const void *txt, DNSServiceRegisterReply callback, void *context) {
    (void)ignored_interface;
    const char *interface = getenv("CAR_AIRPLAY_INTERFACE");
    unsigned int index = interface ? if_nametoindex(interface) : 0;
    if (!index) return kDNSServiceErr_BadInterfaceIndex;
    register_fn real_register = (register_fn)dlsym(RTLD_NEXT, "DNSServiceRegister");
    if (!real_register) return kDNSServiceErr_Unknown;
    return real_register(ref, flags, index, name, type, domain, host, port,
                         txt_length, txt, callback, context);
}
